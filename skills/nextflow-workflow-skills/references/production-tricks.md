# Production tricks — Nextflow patterns from rnaseq/sarek

A lookup-style cookbook of 20 specific patterns lifted from `nf-core/rnaseq` 3.26 and `nf-core/sarek` 3.8 source code that are barely mentioned (or not at all) in the official Nextflow docs. Each section is self-contained — jump to the one you need.

Companion to [`learn-from-rnaseq-sarek.md`](learn-from-rnaseq-sarek.md), which covers the **structural** patterns (directory tree, retry ladder, label tiers, module conventions, samplesheets, migration checklist).

## Index

| # | Trick |
|---|---|
| 1 | `groupKey()` — don't block on the whole channel to group |
| 2 | `ext.prefix` — rename outputs from config, not from the module |
| 3 | `ext.args2`, `ext.args3` — for multi-tool pipes inside one process |
| 4 | `ext.when` — disable a process from config, no code edit |
| 5 | `saveAs:` — conditional publishing controlled by a param |
| 6 | `.first()` — convert queue channel → value channel for broadcast |
| 7 | `file(path, checkIfExists: true)` — fail fast on missing inputs |
| 8 | `getGenomeAttribute(attr)` — graceful fallback for reference params |
| 9 | Dual-form container — single line that works on Docker AND Singularity |
| 10 | `stub:` block — make `-stub-run` actually useful |
| 11 | `.dump(tag: 'foo')` — production-grade channel debugging |
| 12 | `cache: 'lenient'` — survive shared filesystem timestamp drift |
| 13 | `subMap` + map merge `+` — copy/extend `meta` without mutating |
| 14 | `flatMap` + `groupTuple` — pivot a table-like channel |
| 15 | Fully-qualified process selectors in config |
| 16 | `meta.num_intervals` — pre-compute group size for scatter-gather |
| 17 | `validate_params` + `nextflow_schema.json` — free `--help` and CLI validation |
| 18 | `errorStrategy = { task.attempt > 2 ? 'ignore' : 'retry' }` — try then give up gracefully |
| 19 | `mix()` chain for "collect versions across the DAG" |
| 20 | `--outdir` is required; never default it |

### 1. `groupKey()` — don't block on the whole channel to group

Default `.groupTuple()` waits until the **entire upstream channel completes** before emitting any group, because it can't know the group is final. For scatter-gather over N intervals per sample, that means nothing runs until every interval finishes for every sample.

Fix used everywhere in sarek (Mutect2, Lofreq, GetPileupSummaries):

```nextflow
// Tag the key with its expected group size; groupTuple emits as soon as that many arrive
vcf_to_merge = vcf_branch.intervals
    .map { meta, vcf -> [groupKey(meta, meta.num_intervals), vcf] }
    .groupTuple()

MERGE_MUTECT2(vcf_to_merge, dict)
```

Requires you to attach `num_intervals` to `meta` earlier (when you do the scatter). Massive throughput win for any fan-out → fan-in pattern.

### 2. `ext.prefix` — rename outputs from config, not from the module

Same module reused twice (e.g. SAMTOOLS_SORT on both raw and dedup BAMs) needs different output names without forking it:

```groovy
// conf/modules.config
withName: '.*:BAM_MARKDUPLICATES_PICARD:SAMTOOLS_SORT' {
    ext.prefix = { "${meta.id}.markdup.sorted" }
}
withName: '.*:BAM_SORT_STATS_SAMTOOLS:SAMTOOLS_SORT' {
    ext.prefix = { "${meta.id}.namesorted" }
}
```

The module reads `task.ext.prefix ?: "${meta.id}"` and renames its output accordingly. Use this instead of editing module files.

### 3. `ext.args2`, `ext.args3` — for multi-tool pipes inside one process

When a process pipes two tools (`bwa mem ... | samtools view -bS`), give each its own arg slot:

```groovy
withName: 'BWAMEM2_MEM' {
    ext.args  = { params.bwamem2_extra_args ?: '' }
    ext.args2 = { '-bS' }                  // for the samtools view step inside
}
```

The module template references `task.ext.args` and `task.ext.args2` at the corresponding `|` boundaries.

### 4. `ext.when` — disable a process from config, no code edit

Sarek toggles every variant caller this way:

```groovy
withName: 'GATK4_MUTECT2' {
    ext.when = { params.tools && params.tools.split(',').contains('mutect2') }
}
withName: '.*:BAM_VARIANT_CALLING_GERMLINE_MANTA:.*' {
    ext.when = { params.tools && params.tools.split(',').contains('manta') }
}
```

The module's `when:` block is the standard `task.ext.when == null || task.ext.when`. A user enabling/disabling tools via `--tools` never touches Groovy — the config does the routing.

### 5. `saveAs:` — conditional publishing controlled by a param

Don't write the file? Just don't publish it:

```groovy
withName: 'STAR_ALIGN' {
    publishDir = [
        path: { "${params.outdir}/star" },
        mode: 'copy',
        saveAs: { filename -> params.save_align_intermeds ? filename : null }
    ]
}
```

Returning `null` from `saveAs` skips the file entirely. Combine with `filename.equals('versions.yml') ? null : filename` to suppress version files from data directories.

### 6. `.first()` — convert queue channel → value channel for broadcast

A `queue channel` is consumed once; a `value channel` broadcasts to every downstream task. When a process needs the genome FASTA + FAI for all samples:

```nextflow
ch_fasta_fai = ch_fasta
    .combine(ch_fai)
    .map { fasta, fai -> [[:], fasta, fai] }
    .first()                              // <-- key line
```

Without `.first()`, only one sample gets the reference and the rest silently hang. Sarek annotates this trick in a code comment because it's bitten everyone.

### 7. `file(path, checkIfExists: true)` — fail fast on missing inputs

Pipelines crash deep inside a process when a config param points at a non-existent file. Validate at parse time:

```nextflow
ch_samplesheet = channel.value(file(params.input, checkIfExists: true))

if (params.dbnsfp) {
    vep_extra_files.add(file(params.dbnsfp,     checkIfExists: true))
    vep_extra_files.add(file(params.dbnsfp_tbi, checkIfExists: true))
}
```

The error becomes "file does not exist: …" at startup, not "tool failed, exit 1" 40 minutes in.

### 8. `getGenomeAttribute(attr)` — graceful fallback for reference params

Both pipelines define this helper to let users either pass `--genome GRCh38` (resolved via `igenomes.config`) or `--fasta` / `--gtf` / etc. directly:

```nextflow
def getGenomeAttribute(attribute) {
    if (params.genomes && params.genome && params.genomes.containsKey(params.genome)) {
        if (params.genomes[params.genome].containsKey(attribute)) {
            return params.genomes[params.genome][attribute]
        }
    }
    return null
}

params.fasta = getGenomeAttribute('fasta')   // overridden by --fasta if user passes it
params.gtf   = getGenomeAttribute('gtf')
```

Even if you don't use iGenomes, lift this pattern for any "named bundle vs explicit paths" param group.

### 9. Dual-form container — single line that works on Docker AND Singularity

Without this you maintain two container directives. The trick is a ternary on `workflow.containerEngine`:

```nextflow
container "${ workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
    ? 'https://depot.galaxyproject.org/singularity/fastqc:0.12.1--hdfd78af_0'
    : 'biocontainers/fastqc:0.12.1--hdfd78af_0' }"
```

The `task.ext.singularity_pull_docker_container` escape hatch lets a config force Singularity to *build* from the Docker image instead of fetching the prebuilt `.sif` — needed when the Galaxy depot is down.

### 10. `stub:` block — make `-stub-run` actually useful

Every nf-core module ships a `stub:` block alongside `script:`:

```nextflow
process STAR_ALIGN {
    // ...
    script: """ STAR ... """
    stub:   """
        touch ${prefix}.Aligned.sortedByCoord.out.bam
        touch ${prefix}.Log.final.out
        echo '"${task.process}":\n    star: 2.7.11a' > versions.yml
    """
}
```

Then `nextflow run main.nf -stub-run` runs the whole DAG in milliseconds, creating empty files. Use it to:

- Debug channel topology without burning compute
- Smoke-test config changes
- Validate that publishDir patterns match

### 11. `.dump(tag: 'foo')` — production-grade channel debugging

Better than `.view()` because it's gated by an env var:

```nextflow
ch_reads
    .map { meta, reads -> tuple(meta + [normalized: true], reads) }
    .dump(tag: 'reads_normalized')
    | ALIGN
```

Then: `NXF_DUMP_CHANNELS='reads_normalized' nextflow run main.nf` prints only the channels you asked about. Leave `.dump()` calls in committed code — they're inert by default.

### 12. `cache: 'lenient'` — survive shared filesystem timestamp drift

On NFS/Lustre, file mtimes can change without content changing (rsync, backup, touch), invalidating the `-resume` cache. Per-process workaround:

```nextflow
process STAR_INDEX {
    cache 'lenient'   // hash by size + path only, ignore mtime
    // ...
}
```

Or globally in config: `process.cache = 'lenient'`. Used by both pipelines for large reference-building processes that you really don't want re-running.

### 13. `subMap` + map merge `+` — copy/extend `meta` without mutating

Closures share `meta` references. Mutating in one path corrupts another. Always derive a new map:

```nextflow
ch.map { meta, vcf ->
    def new_meta = meta.subMap('id', 'patient', 'status') + [vartype: 'snv']
    tuple(new_meta, vcf)
}
```

`subMap` picks keys you want; `+` returns a new merged map. Used hundreds of times across sarek for tumor-normal pairing where `meta.id` needs to change but other fields propagate.

### 14. `flatMap` + `groupTuple` — pivot a table-like channel

Rare but powerful. To take `[id, fasta]` pairs and produce `[ ['id': [...], 'fasta': [...]] ]`:

```nextflow
ch_table
    .flatMap { id, fafile -> [['id', id], ['fasta', file(fafile, checkIfExists: true)]] }
    .groupTuple()                    // groups by first element ('id' or 'fasta')
```

Used in rnaseq for building a column-oriented sample sheet from a row-oriented one.

### 15. Fully-qualified process selectors in config

`withName: 'STAR_ALIGN'` matches every `STAR_ALIGN` in the pipeline. Often you want it to apply only inside one subworkflow:

```groovy
withName: '.*:BAM_MARKDUPLICATES_PICARD:SAMTOOLS_SORT' { /* only this one */ }
withName: 'NFCORE_SAREK:.*:BWAMEM2_MEM'                { cpus = 24 }
withName: '.*:BAM_VARIANT_CALLING_GERMLINE_MANTA:.*'   { ext.when = { ... } }
```

The fully-qualified name is `WORKFLOW:SUBWORKFLOW:...:PROCESS`. Find it via `nextflow log <run> -f name`.

### 16. `meta.num_intervals` — pre-compute group size for scatter-gather

Set this when you first scatter, so downstream `groupKey()` (§10.1) can use it:

```nextflow
ch_bam
    .combine(ch_intervals_grouped)        // [meta, bam, intervals_list]
    .map { meta, bam, intervals ->
        tuple(meta + [num_intervals: intervals.size()], bam, intervals)
    }
    .transpose()                           // emit one [meta, bam, interval] per interval
```

Now the meta carries enough information for `groupKey(meta, meta.num_intervals)` 10 steps later.

### 17. `validate_params` + `nextflow_schema.json` — free `--help` and CLI validation

Set `params.validate_params = true` (default in nf-core init subworkflow). Then `nextflow_schema.json` drives:

- Typed CLI parsing (`--max_cpus 16` → integer, not string)
- Automatic `nextflow run mypipeline --help` man-page
- Reject unknown `--foo` flags (typo protection)
- Group params in `--help` output by `groups` in the schema

Build it with `nf-core pipelines schema build` — never hand-write JSON Schema.

### 18. `errorStrategy = { task.attempt > 2 ? 'ignore' : 'retry' }` — try then give up gracefully

For optional QC/annotation steps that you'd rather skip than fail the whole pipeline:

```groovy
withName: 'DUPRADAR' {
    errorStrategy = { task.attempt > 2 ? 'ignore' : 'retry' }
    maxRetries    = 2
}
```

Closure form lets you change strategy based on attempt count — retry twice, then ignore. The pipeline keeps going; MultiQC just shows a missing tile.

### 19. `mix()` chain for "collect versions across the DAG"

Every nf-core subworkflow starts an empty channel and mixes every process's `versions.yml` into it:

```nextflow
workflow ALIGN_AND_COUNT {
    main:
        ch_versions = Channel.empty()
        STAR_ALIGN(...);    ch_versions = ch_versions.mix(STAR_ALIGN.out.versions)
        FEATURECOUNTS(...); ch_versions = ch_versions.mix(FEATURECOUNTS.out.versions)
    emit:
        versions = ch_versions
}
```

The top-level workflow then does one final `ch_versions.collectFile(name: 'collated_versions.yml')` for MultiQC. This is the only sane way to track tool versions across a 50-process pipeline.

### 20. `--outdir` is required; never default it

Both pipelines refuse to start without `--outdir`. Lift this: a hard-coded default like `outdir = 'results'` causes users to silently clobber each other's runs. Require it explicitly and validate in the init subworkflow.
