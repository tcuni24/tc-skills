# Strict syntax & typed processes / workflows

Targets **Nextflow 25.04 → 26.04+**. The strict parser and static types are how new Nextflow code is written; legacy DSL2 still runs but is being phased out.

## 1. Enabling the strict parser

| Version | Strict parser default | How to flip |
|---|---|---|
| 25.04, 25.10 | **off** | `export NXF_SYNTAX_PARSER=v2` to opt in |
| 26.04+ | **on** | `export NXF_SYNTAX_PARSER=v1` to opt out (temporary) |

Strict-parser-only features (25.10+): `nextflow.enable.types`, typed `params {}` block, typed processes, typed workflows.

Validate any script ahead of time:

```bash
nextflow lint main.nf            # uses the strict parser
nextflow lint --strict-syntax    # explicit
```

## 2. Common legacy → strict rewrites

### `import` is no longer allowed in scripts

```groovy
// Legacy
import groovy.json.JsonSlurper
def j = new JsonSlurper().parse(file)
```

```nextflow
// Strict — use fully-qualified name
def j = new groovy.json.JsonSlurper().parse(file)
```

### Closures must declare their parameters

```nextflow
// Legacy — implicit `it`
ch.map { it.toUpperCase() }

// Strict — name it
ch.map { s -> s.toUpperCase() }
```

### No top-level Groovy statements outside script/closure blocks

```nextflow
// Strict: move shared helpers into the `workflow {}` body or a separate .nf with `include`
def normalize(meta) { meta + [id: meta.id.toLowerCase()] }
workflow { Channel.of([id:'A']).map { normalize(it) } | view }
```

### `def` outside processes/workflows must be at file scope

```nextflow
// File-scope helper — OK in strict
def parseRow(row) { tuple([id: row.sample], file(row.fastq)) }
```

## 3. Typed `params {}` (25.10+)

```nextflow
nextflow.enable.types = true

params {
    input:           Path              // required: fails fast if missing
    outdir:          String  = 'results'
    save_intermeds:  Boolean           // defaults to false in 26.04+
    aligner:         String  = 'star'  // override on CLI: --aligner hisat2
}
```

- CLI values are coerced to the declared type (`--save_intermeds true`).
- Reference `params.*` only in the entry `workflow {}` or `output {}` block — pass them as explicit args into sub-workflows/processes.

## 4. Typed processes

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
        bam:  tuple(meta, file("${meta.id}.bam"))
        log:  file("${meta.id}.Log.final.out")

    script:
        """
        STAR --runThreadN ${task.cpus} \\
             --genomeDir ${index} \\
             --readFilesIn ${reads.join(' ')} --readFilesCommand zcat \\
             --outSAMtype BAM SortedByCoordinate \\
             --outFileNamePrefix ${meta.id}.
        mv ${meta.id}.Aligned.sortedByCoord.out.bam ${meta.id}.bam
        """
}
```

Notes:

- All standard types except `Channel`/`Value` are usable as annotations.
- `Path` inputs (and `Set<Path>`, `List<Path>`, `Map<String,Path>`) are auto-staged into the work dir.
- Nullable inputs: `input: Path?` — without `?` a `null` input fails the task.
- Named outputs (`bam:`, `log:`) replace the legacy `emit:` keyword; consume as `ALIGN.out.bam`.

### `stage:` block for renaming

```nextflow
process CAT_OPT {
    input:  input: Path?
    stage:  stageAs input, 'input.txt'
    output: file('out.txt')
    script: "cat input.txt > out.txt 2>/dev/null || touch out.txt"
}
```

## 5. Typed workflows

```nextflow
nextflow.enable.types = true

workflow RNASEQ {
    take:
        reads: Channel<tuple(Map, Set<Path>)>
        index: Path
        gtf:   Path

    main:
        FASTP(reads)
        ALIGN(FASTP.out.reads, index)
        COUNT(ALIGN.out.bam, gtf)

    emit:
        counts: COUNT.out.counts
        logs:   FASTP.out.log.mix(ALIGN.out.log)
}
```

## 6. Workflow `output {}` block — declarative publishing (25.10 stable)

`output {}` replaces per-process `publishDir`. One declarative block describes what the pipeline publishes; channels in the entry workflow's `publish:` section are mapped to it. Same hash-based caching, much less repetition, and you get an **index file** (CSV/JSON) describing every output for free.

```nextflow
workflow {
    main:
        read_pairs_ch = channel.fromFilePairs(params.reads, checkIfExists: true, flat: true)
        (fastqc_ch, quant_ch) = RNASEQ(read_pairs_ch, params.transcriptome)
        multiqc_report = MULTIQC(fastqc_ch.mix(quant_ch).collect(), params.multiqc)

    publish:                                  // bind channels to output names
        fastqc_logs    = fastqc_ch
        multiqc_report = multiqc_report
}

output {                                      // declare what each output looks like on disk
    fastqc_logs {
        path 'qc/fastqc'                      // overrides default = output name
        index {                               // optional: auto-generate samples.csv
            path 'qc/fastqc/samples.csv'
            header true
        }
    }
    multiqc_report {
        path 'qc'                             // single file → directory
    }
}
```

Global publish settings live in `nextflow.config` under `workflow.output`:

```groovy
outputDir = 'results'                         // base dir (replaces per-process publishDir paths)
workflow.output {
    mode      = 'copy'                        // 'copy' | 'symlink' | 'link' | 'move' | 'rellink'
    overwrite = 'deep'
}
```

### Migration from `publishDir`

For each process with `publishDir`:

1. Remove the `publishDir` directive from the process.
2. In the entry `workflow {}`, add a `publish:` assignment from the corresponding emit channel: `fastqc_logs = FASTQC.out.zip`.
3. Add an `output { fastqc_logs { ... } }` entry to declare destination and (optionally) index.
4. Remove the `publishDir` config blocks from `modules.config` — `workflow.output { mode = 'copy' }` covers them once.

If a process produced several different file groups via `pattern:` filters, split into multiple `publish:` lines that each filter the channel:

```nextflow
publish:
    bams = ALIGN.out.bam
    logs = ALIGN.out.bam.map { meta, bam, log -> tuple(meta, log) }
```

### When to keep `publishDir`

`publishDir` is still supported (deprecation only, no removal date). Keep it for:

- Pipelines you don't plan to migrate before Nextflow ≥ 26.04 in your environment.
- Modules sourced from `nf-core/modules` that you don't want to fork — apply `publishDir` in `modules.config` instead.

For greenfield pipelines on 25.10+, **prefer `output {}`** — the index file alone (a CSV of every published file with its metadata) is worth the migration.

## 7. Typed records (companion to typed processes)

```nextflow
record SampleMeta {
    id:           String
    strandedness: String  = 'auto'
    paired:       Boolean = true
}

process FASTQC {
    input:
        meta:  SampleMeta
        reads: Set<Path>
    output:
        tuple(meta, file('*.zip'))
    script:
        """
        echo "Sample ${meta.id} (paired=${meta.paired})"
        fastqc --threads ${task.cpus} ${reads.join(' ')}
        """
}
```

Records replace untyped `Map` meta. The IDE auto-completes fields, typos fail at lint time, and `record + record` field merging is type-checked. Migrate `meta: Map` → `meta: SampleMeta` once your pipeline stabilises.

## 8. Migration checklist

1. `nextflow lint main.nf` — fix every error (imports, untyped closures, illegal top-level statements).
2. Add `NXF_SYNTAX_PARSER=v2` to CI, leave production on default until 26.04.
3. Move all `params.*` defaults into a single typed `params {}` block; delete duplicate defaults in config.
4. Convert one process at a time to typed; keep mixed legacy + typed processes in the same pipeline while migrating.
5. Replace `emit: name` with named outputs; consumers (`PROC.out.name`) don't change.
6. Once green, set `nextflow.enable.types = true` at the top of the entry script.

## References

- https://www.nextflow.io/docs/latest/strict-syntax.html
- https://www.nextflow.io/docs/latest/process-typed.html
- https://www.nextflow.io/docs/latest/workflow-typed.html
- https://www.nextflow.io/docs/latest/migrations/
