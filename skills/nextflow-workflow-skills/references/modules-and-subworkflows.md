# Building modular pipelines — modules, subworkflows, entry pattern

The structure used by every nf-core pipeline (rnaseq, sarek included). Designed so each process can be installed/updated independently via `nf-core modules install`.

## 1. Recommended directory layout

```
main.nf                            # entry point, wiring only
nextflow.config
conf/
  base.config
  modules.config
  test.config
workflows/
  rnaseq.nf                        # the main named workflow
subworkflows/
  local/
    prepare_genome.nf
    utils_nfcore_rnaseq_pipeline.nf  # PIPELINE_INITIALISATION / PIPELINE_COMPLETION
  nf-core/
    fastq_align_hisat2/
      main.nf
      meta.yml
modules/
  local/
    bedtools_genomecov.nf
  nf-core/
    fastqc/
      main.nf
      meta.yml
      environment.yml
assets/
  schema_input.json                # samplesheet schema (nf-schema)
nextflow_schema.json               # params schema
```

## 2. Anatomy of a process module (`modules/nf-core/fastqc/main.nf`)

```nextflow
process FASTQC {
    tag "${meta.id}"
    label 'process_low'
    container "${ workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
        ? 'https://depot.galaxyproject.org/singularity/fastqc:0.12.1--hdfd78af_0'
        : 'biocontainers/fastqc:0.12.1--hdfd78af_0' }"

    input:
    tuple val(meta), path(reads)

    output:
    tuple val(meta), path("*.html"), emit: html
    tuple val(meta), path("*.zip") , emit: zip
    path  "versions.yml"           , emit: versions

    when:
    task.ext.when == null || task.ext.when

    script:
    def args   = task.ext.args   ?: ''
    def prefix = task.ext.prefix ?: "${meta.id}"
    """
    fastqc ${args} --threads ${task.cpus} ${reads}
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        fastqc: \$(fastqc --version | sed 's/FastQC v//')
    END_VERSIONS
    """
}
```

Conventions: `tag` for log readability, `label` for resources, `versions.yml` for MultiQC software table, `task.ext.args`/`prefix` for config-driven knobs.

## 3. Anatomy of a subworkflow (`take`/`main`/`emit`)

```nextflow
// subworkflows/local/align_quantify.nf
include { STAR_ALIGN }    from '../../modules/nf-core/star/align/main'
include { SALMON_QUANT }  from '../../modules/nf-core/salmon/quant/main'
include { SAMTOOLS_INDEX } from '../../modules/nf-core/samtools/index/main'

workflow ALIGN_QUANTIFY {
    take:
        ch_reads   // channel: [ meta, [R1, R2] ]
        star_index // value: path
        gtf        // value: path
        tx_fasta   // value: path

    main:
        ch_versions = Channel.empty()

        STAR_ALIGN(ch_reads, star_index, gtf, false, '', '')
        ch_versions = ch_versions.mix(STAR_ALIGN.out.versions)

        SAMTOOLS_INDEX(STAR_ALIGN.out.bam)

        SALMON_QUANT(STAR_ALIGN.out.bam_transcript, tx_fasta, gtf, '', false, 'A')
        ch_versions = ch_versions.mix(SALMON_QUANT.out.versions)

    emit:
        bam      = STAR_ALIGN.out.bam
        bai      = SAMTOOLS_INDEX.out.bai
        quants   = SALMON_QUANT.out.results
        versions = ch_versions
}
```

Rules:

- `take:` declares inputs (one channel per line, in order).
- `main:` is the body; processes/subworkflows are called like functions.
- `emit:` exposes named output channels for callers.
- Multi-output processes: consume as `PROC.out.<name>`.

## 4. Entry pattern — `PIPELINE_INITIALISATION` / `PIPELINE_COMPLETION`

Every modern nf-core pipeline (rnaseq, sarek) uses the same pattern. `main.nf` does only wiring:

```nextflow
// main.nf
include { RNASEQ                  } from './workflows/rnaseq'
include { PREPARE_GENOME          } from './subworkflows/local/prepare_genome'
include { PIPELINE_INITIALISATION } from './subworkflows/local/utils_nfcore_rnaseq_pipeline'
include { PIPELINE_COMPLETION     } from './subworkflows/local/utils_nfcore_rnaseq_pipeline'

workflow NFCORE_RNASEQ {
    take:
        samplesheet
        ch_versions
    main:
        PREPARE_GENOME(...)
        RNASEQ(samplesheet, PREPARE_GENOME.out.index, PREPARE_GENOME.out.gtf)
    emit:
        multiqc_report = RNASEQ.out.multiqc_report
}

workflow {
    main:
        // Validate samplesheet, parse params, build versions channel, set up logging
        PIPELINE_INITIALISATION(
            params.version,
            params.validate_params,
            params.monochrome_logs,
            args,
            params.outdir,
            params.input
        )

        NFCORE_RNASEQ(PIPELINE_INITIALISATION.out.samplesheet, Channel.empty())

        // Send email, write final reports, MultiQC summary
        PIPELINE_COMPLETION(
            params.email,
            params.email_on_fail,
            params.plaintext_email,
            params.outdir,
            params.monochrome_logs,
            params.hook_url,
            NFCORE_RNASEQ.out.multiqc_report
        )
}
```

The init/completion subworkflows wrap the `nf-schema` plugin for samplesheet + params validation; copy them verbatim from rnaseq/sarek as your starting template.

## 5. Samplesheet validation with `nf-schema`

`nextflow.config`:

```groovy
plugins { id 'nf-schema@2.1.1' }
validation {
    failUnrecognisedParams = false
    lenientMode            = false
    showHiddenParams       = false
}
```

`assets/schema_input.json` (excerpt):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "samplesheet",
  "type": "array",
  "items": {
    "type": "object",
    "properties": {
      "sample":  { "type": "string", "pattern": "^\\S+$", "meta": ["id"] },
      "fastq_1": { "type": "string", "format": "file-path", "pattern": "\\.fastq(\\.gz)?$", "exists": true },
      "fastq_2": { "type": "string", "format": "file-path", "pattern": "\\.fastq(\\.gz)?$", "exists": true }
    },
    "required": ["sample", "fastq_1"]
  }
}
```

Consume it in the pipeline:

```nextflow
include { samplesheetToList } from 'plugin/nf-schema'

Channel.fromList(samplesheetToList(params.input, "${projectDir}/assets/schema_input.json"))
    .map { meta, fq1, fq2 -> tuple(meta, fq2 ? [fq1, fq2] : [fq1]) }
    .set { ch_samples }
```

Errors are reported with row + column, not stack traces.

## 6. Workflow composition tactics

### Join two processes on a meta key

```nextflow
ALIGN.out.bam                       // [meta, bam]
    .join(MARKDUP.out.metrics, by: 0)  // join on first element (meta)
    .set { ch_bam_metrics }
```

### Split a channel on a condition

```nextflow
ch.branch { meta, vcf ->
    snv:   meta.vartype == 'snv'
    indel: meta.vartype == 'indel'
    sv:    true
}
```

### Combine a per-sample channel with a value channel (broadcast)

```nextflow
ch_samples.combine(ch_genome)       // value channels broadcast — every sample × genome once
```

### Gather scattered outputs back together

```nextflow
SCATTER(ch_intervals)
    .out.vcf
    .map { meta, vcf -> tuple(meta.subMap('id','patient'), vcf) }
    .groupTuple()
    | GATHER
```

## 7. Testing modules with `nf-test`

```groovy
// modules/nf-core/fastqc/tests/main.nf.test
nextflow_process {
    name "Test Process FASTQC"
    script "../main.nf"
    process "FASTQC"

    test("single-end") {
        when {
            process {
                """
                input[0] = [ [id: 'test'], file(params.test_data['sarscov2']['illumina']['test_1_fastq_gz']) ]
                """
            }
        }
        then {
            assert process.success
            assert snapshot(process.out).match()
        }
    }
}
```

Run: `nf-test test modules/nf-core/fastqc/tests/main.nf.test`.

## 8. The `bin/` directory — helper scripts staged onto `PATH`

Any executable file under `bin/` is automatically added to the `PATH` of every process. Use it for helpers too small to deserve their own container, or for project-specific glue.

```
bin/
├── fastq_dir_to_samplesheet.py     # CLI; user runs locally to build samplesheet.csv
├── deseq2_qc.r                     # called inside a DESEQ2_QC process
└── filter_blast.py                 # called inside a FILTER_BLAST process
```

Rules:

- **Must be executable** (`chmod +x bin/*`) and start with `#!/usr/bin/env python3` / `#!/usr/bin/env Rscript` / etc.
- Referenced **by basename only** in process scripts — `bin/` is on `PATH`, not the absolute path.
- The container the process uses must provide the interpreter (`python3`, `Rscript`, etc.) — `bin/` doesn't ship runtimes.
- Nextflow stages the entire `bin/` directory into every task's work dir, so the script can `import` sibling modules in `bin/`.
- Bundled scripts are included in the **resume hash** — editing a `bin/*.py` invalidates the cache for every process that uses it.

Example use inside a process:

```nextflow
process DESEQ2_QC {
    container 'bioconductor/bioconductor_docker:RELEASE_3_18'
    input:
        tuple val(meta), path(counts)
    output:
        tuple val(meta), path('*.pdf')
    script:
        """
        deseq2_qc.r --counts ${counts} --outdir ./
        """
}
```

Two patterns from `rnaseq/bin/`:

1. **User-facing helper** (`fastq_dir_to_samplesheet.py`): a standalone CLI users run *before* the pipeline to build their samplesheet from a directory of FASTQs. Not called from any process.
2. **Process helper** (`deseq2_qc.r`): an R script that's too custom to bundle in a public container but too generic to inline as a heredoc. Called by exactly one process.

**Don't put** in `bin/`:

- Anything that needs compilation (the script is staged, not built).
- Anything system-specific (it has to run inside every container the pipeline uses).
- Anything > ~50 lines you'd rather develop, lint, and test as a real package — at that point, build a custom container.

## 9. nf-core CLI shortcuts

```bash
nf-core pipelines create               # scaffold a new nf-core-style pipeline
nf-core modules install fastqc         # add a vetted module
nf-core modules update fastqc          # pull latest version of a module
nf-core subworkflows install bam_markduplicates_picard
nf-core pipelines lint                 # check templating, schema, docs
nf-core pipelines schema build         # rebuild nextflow_schema.json from params
```
