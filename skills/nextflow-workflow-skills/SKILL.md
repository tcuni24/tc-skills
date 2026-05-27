---
name: nextflow-workflow-skills
description: Write your own Nextflow (DSL2 / 25.10+) pipelines — processes, channels, typed params, modular subworkflows, production-grade configs, executor profiles. Use when the user asks to author or refactor `.nf` scripts, design a samplesheet-driven pipeline, build modules/subworkflows, write a `nextflow.config`, set up `withLabel`/`withName` resource tiers, port to SLURM/AWS/K8s, or migrate to strict syntax / typed processes. NOT for running nf-core pipelines as a user — but the skill borrows the structural patterns proven by nf-core/rnaseq 3.26 and nf-core/sarek 3.8 as the reference layout for your own pipeline.
license: Apache-2.0
---

# Nextflow Workflow Skills

For **authoring** Nextflow pipelines. Targets **Nextflow ≥ 25.04** (strict syntax default in 26.04+). Uses `nf-core/rnaseq` 3.26 and `nf-core/sarek` 3.8 as **structural references** — patterns to copy when laying out your own pipeline, not pipelines to execute.

## When to use

- Writing a new `.nf` script, module, or subworkflow from scratch
- Refactoring a monolithic `.nf` into the standard `main.nf` + `workflows/` + `subworkflows/` + `modules/` layout
- Writing a `nextflow.config` / `conf/base.config` with proper resource tiers and retry strategy
- Designing a samplesheet (CSV/TSV) and channel-ingestion code with validation
- Adding a new process and wiring its outputs into the DAG with the right channel operators
- Migrating legacy DSL2 to **strict syntax** (`NXF_SYNTAX_PARSER=v2`) or **typed processes/workflows** (`nextflow.enable.types = true`)
- Debugging your own pipeline: `-resume` cache misses, OOM/retry, glob mismatches, container errors

If the task is "run nf-core/X" with no authoring involved, this skill is the wrong tool.

## Quick start — a minimal pipeline you'd actually write

```
my-pipeline/
├── main.nf
├── nextflow.config
├── conf/base.config
├── modules/local/fastqc.nf
└── samplesheet.csv
```

```nextflow
// main.nf — entry point, wiring only
include { FASTQC } from './modules/local/fastqc'

workflow {
    Channel.fromPath(params.input)
        .splitCsv(header: true)
        .map { row -> tuple([id: row.sample], [file(row.fastq_1), file(row.fastq_2)]) }
        | FASTQC
}
```

```nextflow
// modules/local/fastqc.nf
process FASTQC {
    tag   "${meta.id}"
    label 'process_low'
    container 'biocontainers/fastqc:0.12.1--hdfd78af_0'

    input:
    tuple val(meta), path(reads)

    output:
    tuple val(meta), path("*_fastqc.zip"), emit: zip
    path "versions.yml",                   emit: versions

    script:
    def args = task.ext.args ?: ''
    """
    fastqc ${args} --threads ${task.cpus} ${reads}
    cat <<-END > versions.yml
    "${task.process}":
        fastqc: \$(fastqc --version | sed 's/FastQC v//')
    END
    """
}
```

```bash
nextflow run main.nf -profile docker --input samplesheet.csv --outdir results -resume
```

## Core concepts you must know to author

| Concept | One-liner |
|---|---|
| **Process** | Containerized task. Inputs/outputs are typed channels; directives (`cpus`, `memory`, `container`, `label`, `tag`) control execution |
| **Channel** | Async queue: `Channel.of`, `.fromPath`, `.fromFilePairs`, `.splitCsv` |
| **Operator** | `map`, `filter`, `groupTuple`, `combine`, `join`, `mix`, `collect`, `branch`, `multiMap` |
| **`meta` map** | Convention: first element of every tuple channel is `[id: ..., ...]`; carries sample metadata end-to-end |
| **Subworkflow** | Reusable `take:` / `main:` / `emit:` block; one file per subworkflow |
| **`task.ext.args` / `task.ext.prefix`** | Config-driven knobs — change a process's CLI flags from `modules.config` **without editing the module** |
| **Label tier** | `process_single|low|medium|high|long|high_memory|gpu` — match in config with `withLabel:` |
| **`versions.yml`** | Per-process software version capture for MultiQC; standard nf-core convention |
| **Work dir** | `work/xx/yyyyyy…/` — your debugging surface. `cd` in and read `.command.sh`, `.command.log`, `.command.err` |

## Authoring workflows (the 4 patterns you'll actually need)

### 1 — Read a samplesheet into a typed channel

The canonical pattern across nf-core pipelines:

```nextflow
Channel.fromPath(params.input)
    .splitCsv(header: true)
    .map { row ->
        def meta = [id: row.sample, strandedness: row.strandedness ?: 'auto']
        def reads = row.fastq_2 ? [file(row.fastq_1), file(row.fastq_2)] : [file(row.fastq_1)]
        tuple(meta, reads)
    }
    .set { ch_reads }
```

For real validation use the `nf-schema` plugin (see [`references/modules-and-subworkflows.md`](references/modules-and-subworkflows.md)).

### 2 — Compose processes into a subworkflow

```nextflow
// subworkflows/local/align_and_count.nf
include { STAR_ALIGN }      from '../../modules/local/star_align'
include { SAMTOOLS_INDEX }  from '../../modules/local/samtools_index'
include { FEATURECOUNTS }   from '../../modules/local/featurecounts'

workflow ALIGN_AND_COUNT {
    take:
        ch_reads   // [meta, [R1, R2]]
        star_index // path (value channel)
        gtf        // path (value channel)

    main:
        ch_versions = Channel.empty()
        STAR_ALIGN(ch_reads, star_index)
        SAMTOOLS_INDEX(STAR_ALIGN.out.bam)
        FEATURECOUNTS(STAR_ALIGN.out.bam, gtf)

        ch_versions = ch_versions
            .mix(STAR_ALIGN.out.versions)
            .mix(FEATURECOUNTS.out.versions)

    emit:
        counts   = FEATURECOUNTS.out.counts
        bam      = STAR_ALIGN.out.bam
        versions = ch_versions
}
```

### 3 — Write a typed process (25.10+ strict syntax)

```nextflow
nextflow.enable.types = true

process ALIGN {
    container 'quay.io/biocontainers/star:2.7.11a--h0033a41_0'
    label 'process_high'

    input:
        meta:  Map
        reads: Set<Path>
        index: Path

    output:
        bam: tuple(meta, file("${meta.id}.bam"))
        log: file("${meta.id}.Log.final.out")

    script:
        """
        STAR --runThreadN ${task.cpus} --genomeDir ${index} \\
             --readFilesIn ${reads.join(' ')} --readFilesCommand zcat \\
             --outSAMtype BAM SortedByCoordinate \\
             --outFileNamePrefix ${meta.id}.
        mv ${meta.id}.Aligned.sortedByCoord.out.bam ${meta.id}.bam
        """
}
```

Run: `NXF_SYNTAX_PARSER=v2 nextflow run main.nf`. Full migration guide in [`references/strict-and-typed.md`](references/strict-and-typed.md).

### 4 — Minimal production `nextflow.config`

```groovy
params {
    input  = null
    outdir = null
    // your defaults
}

process {
    cpus   = { 1    * task.attempt }
    memory = { 6.GB * task.attempt }
    time   = { 4.h  * task.attempt }
    errorStrategy = { task.exitStatus in ((130..145) + 104) ? 'retry' : 'finish' }
    maxRetries = 1

    withLabel: process_low    { cpus = { 2  * task.attempt }; memory = { 12.GB * task.attempt } }
    withLabel: process_medium { cpus = { 6  * task.attempt }; memory = { 36.GB * task.attempt } }
    withLabel: process_high   { cpus = { 12 * task.attempt }; memory = { 72.GB * task.attempt } }
    withLabel: process_gpu    { accelerator = 1 }
}

profiles {
    docker      { docker.enabled = true; docker.runOptions = '-u $(id -u):$(id -g)' }
    singularity { singularity.enabled = true; singularity.autoMounts = true }
    slurm       { process.executor = 'slurm'; process.queue = 'batch' }
    awsbatch    { process.executor = 'awsbatch'; aws.region = 'us-east-1' }
    test        { includeConfig 'conf/test.config' }
}
```

Full template (per-process tweaks, SLURM/AWS/GCP/K8s recipes, GPU, `resourceLimits`) in [`references/config-patterns.md`](references/config-patterns.md).

## Channel patterns you'll reuse

```nextflow
// Paired-end glob → [sample_id, [R1, R2]]
Channel.fromFilePairs('data/*_{R1,R2}.fastq.gz')

// Join two process outputs on meta (first element)
ALIGN.out.bam.join(MARKDUP.out.metrics, by: 0)

// Branch into mutually exclusive lanes
ch.branch { meta, bam ->
    tumor:  meta.status == 'tumor'
    normal: true
}

// Fan-out: every sample × every reference
samples_ch.combine(refs_value_ch)

// Scatter-gather over intervals
SCATTER.out.vcf
    .map { meta, vcf -> tuple(meta.subMap('id','patient'), vcf) }
    .groupTuple()
    | GATHER
```

## Key CLI flags for development

| Flag | Purpose |
|---|---|
| `-profile a,b` | Apply profiles (left-to-right merge) |
| `-resume` | Reuse cached tasks (the killer feature for iterative authoring) |
| `-params-file params.yml` | Load all `--xxx` from YAML/JSON (preferred over inline CLI args) |
| `-c custom.config` | Apply extra config — **never put `params{}` here** |
| `-with-report report.html -with-timeline tl.html -with-trace -with-dag dag.html` | Per-run observability bundle |
| `nextflow log <run> -f name,status,exit,duration,hash,workdir` | Inspect a past run; first stop when debugging |
| `nextflow clean -f -before <run>` | Reclaim `work/` disk |
| `nextflow lint main.nf` | Run the strict-syntax linter before committing |

## Troubleshooting (your own pipeline)

| Symptom | Cause | Fix |
|---|---|---|
| `-resume` reruns a task you didn't change | Input file mtime/hash changed, or container tag drifted | `nextflow log <run> -f name,hash`; pin container digest; check `params` diff |
| Process killed with exit 137/140 | OOM | `memory = { N.GB * task.attempt }` + retry on exit 137/140 (already in template) |
| `Process output not found` | `output:` glob doesn't match what the script produced | `cd work/xx/yyyy*/`; `ls`; tighten/quote the glob |
| `Cannot find any process matching: X` | Missing `include {}` or `nextflow.enable.dsl = 1` leftover | Add `include { X } from './modules/...'`; remove DSL1 directives |
| `withName: STAR_ALIGN` ignored | DSL2 uses fully-qualified names like `NFCORE:RNASEQ:ALIGN:STAR_ALIGN` | Run once, then `nextflow log <run> -f name` to see the real names; use `withName: '.*:STAR_ALIGN'` |
| `Unrecognized syntax` after upgrading to 26.04 | Strict parser now default | Fix per [`references/strict-and-typed.md`](references/strict-and-typed.md), or set `NXF_SYNTAX_PARSER=v1` temporarily |
| `OutOfMemoryError` in the head JVM | Nextflow head heap too small | `export NXF_OPTS='-Xms1g -Xmx8g'` |
| Container pull fails | Registry auth / rate limit / network | Pre-pull with `docker pull …`; for Singularity set a persistent `singularity.cacheDir` |
| SLURM job pending forever | Asked for more CPU/memory than the queue allows | `process.resourceLimits = [cpus:N, memory:N.GB, time:Nh]` to cap the retry ladder |
| Channel "empty" downstream | A `filter`/`branch` dropped everything, or a glob returned nothing | `.view { "DEBUG: ${it}" }` between operators; check `ifEmpty` |

## Going deeper — bundled references

Read only what you need:

- [`references/strict-and-typed.md`](references/strict-and-typed.md) — Strict syntax migration; typed `params {}`, processes, workflows; **`workflow.output {}` block** (25.10 stable, replaces `publishDir`); typed records; `stage:` block; nullable inputs
- [`references/config-patterns.md`](references/config-patterns.md) — Full `nextflow.config` + `conf/base.config` template lifted from rnaseq/sarek; per-process `withName`/`withLabel`; SLURM/AWS/GCP/K8s executor recipes; retry ladder; GPU; reports; common anti-patterns
- [`references/modules-and-subworkflows.md`](references/modules-and-subworkflows.md) — Standard pipeline directory layout; module anatomy (`task.ext.args`, `versions.yml`, container dual-form); `PIPELINE_INITIALISATION`/`PIPELINE_COMPLETION` entry pattern; `nf-schema` samplesheet validation; **`bin/` helper scripts convention**; channel composition tactics
- [`references/learn-from-rnaseq-sarek.md`](references/learn-from-rnaseq-sarek.md) — **Structural patterns** to copy when scaffolding your own pipeline: directory tree, `main.nf` shape, retry strategy choice, label tiers, module file conventions, samplesheet schemas (rnaseq vs sarek shapes + restart-from-BAM), 10-step refactoring checklist
- [`references/production-tricks.md`](references/production-tricks.md) — **Tactical cookbook** of 20 patterns barely documented elsewhere: `groupKey()` for non-blocking scatter-gather, `ext.prefix/args2/when`, `.first()` queue→value, `cache: 'lenient'`, dual-form containers, `stub:` blocks, `.dump(tag:)`, `subMap` meta hygiene, version aggregation chain, etc. Indexed — jump to the one trick you need
- [`references/testing.md`](references/testing.md) — **nf-test** for modules, subworkflows, and full-pipeline runs; `nf-test.config` setup; snapshot patterns (`stable_name` + `stable_path` + `.nftignore`); paired `-stub` tests; fixture strategies; tag-based selective runs; GitHub Actions CI matrix
- [`references/iterative-development.md`](references/iterative-development.md) — **Debug loop**: `work/.../.command.{sh,run,log,err}` + `.exitcode` spelunking; `.view()` vs `.dump(tag:)` + `NXF_DUMP_CHANNELS`; `-stub-run` for millisecond topology checks; `-process.echo` / `-with-trace` / `NXF_DEBUG`; `-resume` cache forensics (full hash recipe + `cache: 'lenient'/'deep'`); 8-row common-failure table; `dev` profile config for fast iteration; report-driven profiling

## References

- Nextflow docs — https://www.nextflow.io/docs/latest/
- Strict syntax — https://www.nextflow.io/docs/latest/strict-syntax.html
- Typed processes — https://www.nextflow.io/docs/latest/process-typed.html
- Typed workflows — https://www.nextflow.io/docs/latest/workflow-typed.html
- Structural reference: nf-core/rnaseq 3.26 — https://github.com/nf-core/rnaseq
- Structural reference: nf-core/sarek 3.8 — https://github.com/nf-core/sarek
- Di Tommaso P. et al. (2017) *Nat Biotechnol* 35:316–319. https://doi.org/10.1038/nbt.3820
