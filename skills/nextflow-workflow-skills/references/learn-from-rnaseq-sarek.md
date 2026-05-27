# What to copy from nf-core/rnaseq and nf-core/sarek

These two pipelines are the most battle-tested Nextflow codebases in existence. When authoring your own pipeline, **steal their structure** — don't reinvent it. This file lists what specifically to copy, with source pointers.

Reference revisions: `nf-core/rnaseq` 3.26.0, `nf-core/sarek` 3.8.1.

## 1. Directory tree — copy verbatim

Both pipelines use the same top-level layout. Use it for yours too:

```
my-pipeline/
├── main.nf                       # entry: include + workflow{} only, no logic
├── nextflow.config               # top-level: params{}, profiles{}, includeConfig
├── nextflow_schema.json          # params schema (drives --help, nf-schema)
├── conf/
│   ├── base.config               # default resources + retry ladder
│   ├── modules.config            # publishDir + ext.args per process
│   ├── test.config               # tiny inputs for -profile test
│   └── igenomes.config           # (optional) iGenomes paths
├── workflows/
│   └── mypipeline.nf             # the main named workflow
├── subworkflows/
│   ├── local/                    # subworkflows specific to this pipeline
│   │   ├── prepare_genome.nf
│   │   └── utils_nfcore_*_pipeline.nf   # PIPELINE_INITIALISATION / COMPLETION
│   └── nf-core/                  # vendored from nf-core/subworkflows
├── modules/
│   ├── local/                    # processes specific to this pipeline
│   └── nf-core/                  # vendored from nf-core/modules
├── assets/
│   ├── schema_input.json         # samplesheet schema for nf-schema
│   ├── multiqc_config.yml
│   └── samplesheet.csv           # example
└── bin/                          # auxiliary scripts staged onto PATH
```

**Why this layout works**: `main.nf` stays trivial and rarely changes; new analysis steps are added by dropping a file into `modules/` and one `include {}` line; resource tuning happens in `conf/` without touching pipeline logic; `nf-core modules update` can refresh vendored tools without merge conflicts.

## 2. `main.nf` is wiring only

Both rnaseq and sarek follow this exact shape — copy it:

```nextflow
#!/usr/bin/env nextflow

// 1. Optional: pre-resolve iGenomes-style derived params
params.fasta = getGenomeAttribute('fasta')
params.gtf   = getGenomeAttribute('gtf')
// ...

// 2. Includes
include { MYPIPELINE              } from './workflows/mypipeline'
include { PREPARE_GENOME          } from './subworkflows/local/prepare_genome'
include { PIPELINE_INITIALISATION } from './subworkflows/local/utils_nfcore_mypipeline_pipeline'
include { PIPELINE_COMPLETION     } from './subworkflows/local/utils_nfcore_mypipeline_pipeline'

// 3. A named workflow wrapping your real pipeline (lets others `include` you)
workflow MY_NAMED_PIPELINE {
    take:
        samplesheet
    main:
        PREPARE_GENOME(params.fasta, params.gtf)
        MYPIPELINE(samplesheet, PREPARE_GENOME.out.index, PREPARE_GENOME.out.gtf)
    emit:
        multiqc_report = MYPIPELINE.out.multiqc_report
}

// 4. The unnamed entry workflow — only init, named pipeline, completion
workflow {
    PIPELINE_INITIALISATION(
        params.version, params.validate_params, params.monochrome_logs,
        args, params.outdir, params.input
    )
    MY_NAMED_PIPELINE(PIPELINE_INITIALISATION.out.samplesheet)
    PIPELINE_COMPLETION(
        params.email, params.email_on_fail, params.plaintext_email,
        params.outdir, params.monochrome_logs, params.hook_url,
        MY_NAMED_PIPELINE.out.multiqc_report
    )
}
```

The init/completion subworkflows handle: nf-schema params validation, samplesheet parsing, version channel setup, MultiQC report aggregation, completion email. **Copy them verbatim from rnaseq or sarek into your `subworkflows/local/`** and rename — they are the most reused 200 lines of Nextflow code in existence.

## 3. The retry ladder — pick the rnaseq or sarek variant

Both `conf/base.config` files use the same structure but differ in the exit codes treated as retryable:

| Pipeline | `errorStrategy` rule |
|---|---|
| rnaseq | `task.exitStatus in ((130..145) + 104 + (175..177)) ? 'retry' : 'finish'` |
| sarek  | `task.exitStatus in ((130..145) + 104 + 175) ? 'retry' : 'finish'` |

`130–145` covers all POSIX signal-killed exits (OOM = 137, SIGTERM = 143, SIGUSR2 = 140). `104` is `ECONNRESET` (transient cloud filesystem hiccup). `175–177` are nf-core scratch-related codes. Use rnaseq's version — it's the superset. Default `maxRetries = 1`; bump to `2` for known-flaky processes via `label 'error_retry'`.

Combine with `cpus/memory/time = { N * task.attempt }` so OOM-killed tasks request more on the next attempt.

## 4. Label tiers — use exactly these names

nf-core modules expect these label names. Define them in your `conf/base.config` and your modules will Just Work when borrowed from `nf-core/modules`:

| Label | Typical cpus | Typical memory | Use for |
|---|---|---|---|
| `process_single`    | 1  | 6 GB   | trivial single-thread tools (`samtools index`, tabix) |
| `process_low`       | 2  | 12 GB  | FastQC, fastp, BCFtools, lightweight QC |
| `process_medium`    | 6  | 36 GB  | Trim Galore, Salmon quant, featureCounts |
| `process_high`      | 12 | 72 GB  | STAR, BWA-MEM, GATK HaplotypeCaller |
| `process_long`      | —  | —      | additive: bumps time only (StringTie, joint genotyping) |
| `process_high_memory` | — | 200 GB | additive: bumps memory only (de novo assembly, big indexes) |
| `process_gpu`       | —  | —      | sets `accelerator = 1` and container GPU flags |
| `error_ignore`      | —  | —      | optional steps; failure won't stop the pipeline |
| `error_retry`       | —  | —      | retry strategy = retry, maxRetries = 2 |

Don't invent new tier names — config consumers and nf-core modules won't know them.

## 5. Module file conventions — what every nf-core module looks like

Pattern shared by all `modules/nf-core/*` files (rnaseq has ~80, sarek has ~120). Copy this skeleton for every new module you write:

```nextflow
process TOOL_SUBCOMMAND {
    tag   "${meta.id}"             // shows in logs
    label 'process_medium'         // resource tier (see §4)

    // Dual-form container: Singularity URL when running with singularity, Docker tag otherwise
    container "${ workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
        ? 'https://depot.galaxyproject.org/singularity/tool:1.2.3--hdfd78af_0'
        : 'biocontainers/tool:1.2.3--hdfd78af_0' }"

    input:
    tuple val(meta), path(reads)
    path  reference

    output:
    tuple val(meta), path("*.bam"), emit: bam
    tuple val(meta), path("*.log"), emit: log
    path  "versions.yml",           emit: versions

    when:
    task.ext.when == null || task.ext.when     // allows config-driven skipping

    script:
    def args   = task.ext.args   ?: ''         // extra CLI flags from modules.config
    def prefix = task.ext.prefix ?: "${meta.id}"
    """
    tool subcommand \\
        ${args} \\
        --threads ${task.cpus} \\
        --reference ${reference} \\
        --output ${prefix}.bam \\
        ${reads}

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        tool: \$(tool --version | sed 's/tool v//')
    END_VERSIONS
    """
}
```

The **four non-obvious conventions** worth obeying:

1. `tag "${meta.id}"` — without it, log lines are anonymous and debugging at scale is painful.
2. `task.ext.args` / `task.ext.prefix` — lets you tweak CLI flags via `modules.config` without forking the module.
3. `task.ext.when` — lets configs disable a process conditionally.
4. `versions.yml` — feeds MultiQC's software-versions table; aggregate across the pipeline via `mix()`.

## 6. Samplesheet schemas — copy the format

Both pipelines define samplesheets in `assets/schema_input.json` (JSON Schema 2020-12) and validate at runtime with the `nf-schema` plugin. Pick the shape that matches your data:

### rnaseq-style (one row per FASTQ pair, merged by sample)

```csv
sample,fastq_1,fastq_2,strandedness
CTRL_1,reads/CTRL_1_L001_R1.fq.gz,reads/CTRL_1_L001_R2.fq.gz,auto
CTRL_1,reads/CTRL_1_L002_R1.fq.gz,reads/CTRL_1_L002_R2.fq.gz,auto
```

Lesson: same `sample` value across rows → merge technical replicates with `groupTuple`. The `auto` strandedness is decided by a subsample-and-pseudoalign QC step early in the workflow — borrow that pattern for any data-driven runtime decision.

### sarek-style (patient grouping + lane awareness + tumor/normal status)

```csv
patient,status,sample,lane,fastq_1,fastq_2
P001,0,N1,L001,reads/N1_L001_R1.fq.gz,reads/N1_L001_R2.fq.gz
P001,1,T1,L001,reads/T1_L001_R1.fq.gz,reads/T1_L001_R2.fq.gz
```

Lessons:

- Use a hierarchical `meta` map: `[patient: 'P001', sample: 'N1', status: 0, lane: 'L001', id: 'N1-L001']`.
- Carry the hierarchy through the channel; collapse with `meta.subMap('patient','sample')` + `groupTuple()` when merging lanes.
- An integer `status` (0/1) is friendlier than booleans for `branch { tumor: meta.status == 1; normal: true }`.

### Restart-from-intermediate samplesheets

sarek also accepts:

```csv
patient,sample,bam,bai
P001,N1,bam/N1.bam,bam/N1.bam.bai
```

…paired with `--step markduplicates` (or `recalibrate`, `variant_calling`, `annotate`) to skip earlier stages. If your pipeline is long, **design `--step` and matching samplesheet variants from day one** — users will need to restart from intermediate outputs.

## 7. Channel patterns worth lifting

### Auto-detect a metadata field by subsampling (rnaseq strandedness)

Subsample reads → pseudoalign → infer property → join back as new meta field. Pattern:

```nextflow
SUBSAMPLE(ch_reads)
SALMON_QUANT_QUICK(SUBSAMPLE.out.reads, transcriptome)
SALMON_QUANT_QUICK.out.json
    .map { meta, json -> tuple(meta.id, parseStrandedness(json)) }
    .set { ch_inferred }

ch_reads
    .map { meta, reads -> tuple(meta.id, meta, reads) }
    .join(ch_inferred)
    .map { id, meta, reads, strand -> tuple(meta + [strandedness: strand], reads) }
    .set { ch_reads_with_strand }
```

### Scatter-gather over genomic intervals (sarek variant calling)

```nextflow
ch_bam_intervals = ch_bam.combine(ch_intervals)         // [meta, bam, interval]
HAPLOTYPECALLER(ch_bam_intervals)
HAPLOTYPECALLER.out.vcf
    .map { meta, vcf -> tuple(meta.subMap('patient','sample'), vcf) }
    .groupTuple()
    | GATK_MERGE_VCFS                                    // gather per-sample
```

### Tumor/normal pairing (sarek somatic)

```nextflow
ch_bam.branch { meta, bam, bai ->
    tumor:  meta.status == 1
    normal: meta.status == 0
}.set { ch_split }

ch_split.tumor
    .map { meta, bam, bai -> tuple(meta.patient, meta, bam, bai) }
    .combine(
        ch_split.normal.map { meta, bam, bai -> tuple(meta.patient, meta, bam, bai) },
        by: 0
    )                                                    // pair on patient
    .map { patient, mt, bt, bit, mn, bn, bin ->
        tuple([id: "${mt.sample}_vs_${mn.sample}", patient: patient], bt, bit, bn, bin)
    }
    | MUTECT2_PAIR
```

## 8. What NOT to copy

- **Don't copy the `igenomes.config` map** unless you actually use iGenomes — it bloats your config.
- **Don't copy `--genome` magic** for small pipelines; explicit `--fasta`/`--gtf` is clearer.
- **Don't copy every nf-core helper subworkflow** (e.g. `utils_nfschema_plugin`) if you're not publishing to nf-core. Pick `PIPELINE_INITIALISATION`/`COMPLETION` and `nf-schema` validation; skip the rest until you need them.
- **Don't copy the `nf-core template` boilerplate** (Slack badges, codespaces config, etc.) for internal pipelines.

## 9. Concrete checklist for porting an existing script

When refactoring a single monolithic `pipeline.nf` into this structure:

1. Create the directory tree from §1 (empty files OK).
2. Move each `process X { ... }` into `modules/local/x.nf` (one process per file).
3. Add `tag`, `label`, `task.ext.args`, `versions.yml` to each module (§5).
4. Group related processes into `subworkflows/local/<group>.nf` with `take:`/`main:`/`emit:`.
5. Reduce `pipeline.nf` → `main.nf` to: includes + entry `workflow {}` only (§2).
6. Extract resource directives from processes into `conf/base.config` via `withLabel` (§4).
7. Extract `publishDir` and tool-specific args into `conf/modules.config` (`withName: ... { ext.args = ... }`).
8. Build `assets/schema_input.json` for the samplesheet; add `nf-schema` validation (§6).
9. Copy `PIPELINE_INITIALISATION`/`COMPLETION` from rnaseq or sarek; rename; wire into `main.nf` (§2).
10. `nextflow lint main.nf` — fix any strict-syntax warnings.

When all 10 are done, your pipeline looks indistinguishable from an nf-core community pipeline and behaves the same way under `-resume`, retries, and executor swaps.


---

For 20 specific tactical patterns lifted from rnaseq/sarek source (`groupKey`, `ext.prefix/args2/when`, `.first()`, `cache: 'lenient'`, dual-form containers, stub blocks, `.dump`, scatter-gather, version aggregation, …), see [`production-tricks.md`](production-tricks.md).
