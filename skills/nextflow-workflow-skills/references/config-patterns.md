# Config patterns — `nextflow.config`, profiles, resources

Distilled from `nf-core/rnaseq` 3.26 and `nf-core/sarek` 3.8 base configs. These are the patterns that actually survive contact with production HPC and cloud.

## 1. Two-file layout (nf-core convention)

```
nextflow.config          # top-level: params, includeConfig, profiles
conf/
  base.config            # default resources (cpus/memory/time) + retry strategy
  modules.config         # per-process tweaks (publishDir, ext.args, container overrides)
  test.config            # tiny inputs for `-profile test`
  igenomes.config        # iGenomes reference paths
```

`nextflow.config` glue:

```groovy
params {
    input  = null
    outdir = null
    // ... defaults only; never required values
}

profiles {
    docker      { docker.enabled      = true; docker.runOptions = '-u $(id -u):$(id -g)' }
    singularity { singularity.enabled = true; singularity.autoMounts = true }
    apptainer   { apptainer.enabled   = true; apptainer.autoMounts = true }
    conda       { conda.enabled       = true; channels = ['conda-forge','bioconda'] }
    slurm       { process.executor    = 'slurm';   process.queue  = 'batch' }
    awsbatch    { process.executor    = 'awsbatch';aws.region     = 'us-east-1' }
    gcp         { process.executor    = 'google-batch'; google.region = 'us-central1' }
    k8s         { process.executor    = 'k8s';     k8s.namespace  = 'nextflow' }
    test        { includeConfig 'conf/test.config' }
    test_full   { includeConfig 'conf/test_full.config' }
}

includeConfig 'conf/base.config'
includeConfig 'conf/modules.config'
```

## 2. `conf/base.config` — resource ladder + retry

```groovy
process {
    cpus   = { 1    * task.attempt }
    memory = { 6.GB * task.attempt }
    time   = { 4.h  * task.attempt }

    // OOM-aware retry: 137 = SIGKILL, 140 = SIGUSR2, 104 = ECONNRESET, 175-177 = nf-core scratch
    errorStrategy = { task.exitStatus in ((130..145) + 104 + (175..177)) ? 'retry' : 'finish' }
    maxRetries    = 1
    maxErrors     = '-1'

    withLabel: process_single     { cpus = { 1 };                memory = { 6.GB   * task.attempt } }
    withLabel: process_low        { cpus = { 2  * task.attempt }; memory = { 12.GB  * task.attempt }; time = { 4.h * task.attempt } }
    withLabel: process_medium     { cpus = { 6  * task.attempt }; memory = { 36.GB  * task.attempt }; time = { 8.h * task.attempt } }
    withLabel: process_high       { cpus = { 12 * task.attempt }; memory = { 72.GB  * task.attempt }; time = { 16.h * task.attempt } }
    withLabel: process_long       { time   = { 20.h * task.attempt } }
    withLabel: process_high_memory{ memory = { 200.GB * task.attempt } }
    withLabel: error_ignore       { errorStrategy = 'ignore' }
    withLabel: error_retry        { errorStrategy = 'retry'; maxRetries = 2 }

    withLabel: process_gpu {
        accelerator      = 1
        containerOptions = { workflow.containerEngine in ['singularity','apptainer'] ? '--nv' : '--gpus all' }
    }
}
```

### Why `* task.attempt`?

On retry, `task.attempt` becomes 2 then 3, automatically bumping cpus/memory/time. Combined with the `errorStrategy` above, OOM-killed tasks self-heal on the next attempt without manual intervention.

### Hard cap with `resourceLimits` (25.04+)

```groovy
process.resourceLimits = [
    cpus:   16,
    memory: 128.GB,
    time:   72.h
]
```

Prevents the retry ladder from requesting more than the node/queue can give.

## 3. Per-process tuning — `withName` vs `withLabel`

Use `withLabel` when many processes share a tier; use `withName` for a single process.

```groovy
process {
    withName: 'BWAMEM2_MEM'        { cpus = 24; memory = 30.GB }
    withName: 'GATK4_HAPLOTYPECALLER' { cpus = 4;  memory = 16.GB; time = 12.h }
    withName: 'BCFTOOLS.*'         { cpus = 1;  memory = 1.GB }          // regex
    withName: 'UNZIP.*|UNTAR.*'    { cpus = 1;  memory = 1.GB }          // alternation
}
```

`withName` supports full regex and is matched against the **fully-qualified module path** in DSL2 (e.g. `NFCORE_RNASEQ:RNASEQ:ALIGN_STAR:STAR_ALIGN`).

## 4. Publishing outputs — `modules.config`

```groovy
process {
    withName: 'STAR_ALIGN' {
        ext.args = '--outSAMtype BAM SortedByCoordinate --outSAMattrIHstart 0'
        publishDir = [
            path:    { "${params.outdir}/star/${meta.id}" },
            mode:    'copy',
            pattern: '*.{bam,bai,log}'
        ]
    }
    withName: 'MULTIQC' {
        publishDir = [ path: { "${params.outdir}/multiqc" }, mode: 'copy' ]
    }
}
```

`ext.args` / `ext.prefix` are the standard nf-core knobs for forwarding extra CLI flags into a module without forking it.

## 5. Executor profiles

### SLURM (institutional HPC)

```groovy
process {
    executor       = 'slurm'
    queue          = 'normal'
    clusterOptions = '--account=myproject'
    scratch        = '/scratch/$USER/$SLURM_JOB_ID'
}
singularity { enabled = true; autoMounts = true; cacheDir = '/scratch/singularity_cache' }
executor.queueSize = 100        // concurrent SLURM jobs cap
```

### AWS Batch

```groovy
process {
    executor = 'awsbatch'
    queue    = 'arn:aws:batch:us-east-1:123:job-queue/nextflow'
}
aws {
    region = 'us-east-1'
    batch  { cliPath = '/home/ec2-user/miniconda/bin/aws'; maxParallelTransfers = 8 }
    client { maxConnections = 20 }
}
workDir = 's3://my-bucket/work'
```

### Google Batch

```groovy
process {
    executor      = 'google-batch'
    machineType   = 'n2-standard-8'
    disk          = '375 GB LOCAL_SSD'
}
google { region = 'us-central1'; project = 'my-gcp-project' }
workDir = 'gs://my-bucket/work'
```

### Kubernetes

```groovy
process.executor = 'k8s'
k8s {
    namespace      = 'nextflow'
    serviceAccount = 'nextflow-sa'
    storageClaimName = 'nextflow-pvc'
    storageMountPath = '/workspace'
}
```

## 6. Reports & observability

```bash
nextflow run main.nf \
  -with-report   report.html   \
  -with-timeline timeline.html \
  -with-trace    trace.txt     \
  -with-dag      dag.html      \
  -with-tower                  # if NEXTFLOW_TOWER_TOKEN is set
```

Permanent config:

```groovy
report   { enabled = true; file = "${params.outdir}/pipeline_info/report.html"   ; overwrite = true }
timeline { enabled = true; file = "${params.outdir}/pipeline_info/timeline.html" ; overwrite = true }
trace    { enabled = true; file = "${params.outdir}/pipeline_info/trace.txt"     ; overwrite = true }
dag      { enabled = true; file = "${params.outdir}/pipeline_info/dag.html"      ; overwrite = true }
```

## 7. Anti-patterns

| Don't | Do |
|---|---|
| Set `params.xxx = ...` in a custom `-c` config for an nf-core pipeline | Use `--xxx` on CLI or `-params-file params.yml` |
| Hard-code absolute paths in process scripts | Stage as `Path` inputs; reference by basename |
| Run on shared HPC without `singularity.cacheDir` | Set a persistent, group-readable cache dir |
| Mix `cpus = 16` with `* task.attempt` scaling on the same process | Pick one strategy; nf-core uses scaling on retry |
| Tune `withName` without checking the fully-qualified DSL2 path | Run once and look at `nextflow log <run> -f name` for the real names |
