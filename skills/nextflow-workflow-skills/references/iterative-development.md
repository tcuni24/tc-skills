# Iterative development & debugging loop

The 80% of time you spend authoring a Nextflow pipeline is iterating: change a process, run, watch it fail, find out why, change again. This file is the toolkit for that loop.

## 1. The 60-second debug recipe

When a task fails, do this in order:

```bash
# 1. Get the run name and the failed task's workdir
nextflow log <run_name> -F 'status == "FAILED"' -f name,exit,workdir,hash

# 2. cd into the work dir
cd work/ab/cdef1234567890.../

# 3. Inspect the four files Nextflow leaves behind
cat .command.sh        # the rendered script (with variable substitution done)
cat .command.run       # the wrapper Nextflow actually ran (env, container, staging)
cat .command.log       # full combined stdout+stderr
cat .command.err       # stderr only
cat .exitcode          # numeric exit code

# 4. Reproduce locally
bash .command.sh       # or: bash .command.run for the full container-wrapped version
```

90% of "my process fails" questions are answered by `cat .command.log` and `bash .command.sh`. Memorize this loop.

## 2. Work directory anatomy

```
work/ab/cdef1234.../
├── .command.sh          # the rendered process script — your code
├── .command.run         # the wrapper: env, container, staging, .command.sh invocation
├── .command.begin       # marker: task started
├── .command.log         # stdout + stderr (interleaved)
├── .command.out         # stdout only (if separated)
├── .command.err         # stderr only
├── .command.trace       # CPU/mem/IO metrics (with -with-trace)
├── .exitcode            # numeric exit code
├── input_R1.fq.gz       # staged inputs (symlinks unless scratch is set)
├── input_R2.fq.gz
└── sample.bam           # outputs (matched against `output:` glob)
```

Everything is a plain file. Cron jobs, ssh, scp, grep — all work.

## 3. Channel debugging — `.view()` then `.dump()`

`.view()` is the loud version (always prints, good during development):

```nextflow
ch_reads
    .map { meta, reads -> tuple(meta + [normalized: true], reads) }
    .view { meta, reads -> "DEBUG ${meta.id}: ${reads*.name}" }
    | ALIGN
```

`.dump(tag: 'name')` is the quiet, env-gated version (leave it in committed code):

```nextflow
ch_reads.dump(tag: 'reads_raw')
```

```bash
# Only prints when you ask
NXF_DUMP_CHANNELS='reads_raw,reads_normalized' nextflow run main.nf
NXF_DUMP_CHANNELS='*'  nextflow run main.nf   # everything
```

Stick `.dump(tag: ...)` at every transition in a complex subworkflow. They cost nothing when not enabled.

## 4. `-stub-run` — millisecond-fast topology testing

Add a `stub:` block to every module (see [`learn-from-rnaseq-sarek.md`](learn-from-rnaseq-sarek.md) §10.10), then:

```bash
nextflow run main.nf -stub-run --input samplesheet.csv --outdir out
```

The whole DAG runs in seconds, producing empty/placeholder files. Use to verify:

- Every `include {}` resolves
- Channel topology (joins, branches, groupTuples) emits the expected cardinality
- `publishDir` / `output {}` patterns match
- `-resume` cache key correctness (toggle a param, re-run, confirm only affected tasks rerun)

## 5. Live execution visibility

```bash
# Echo every task's stdout to your terminal
nextflow run main.nf -process.echo

# Show resource utilisation while running
nextflow run main.nf -with-trace -with-report report.html -with-timeline timeline.html

# Increase log verbosity
NXF_DEBUG=2  nextflow run main.nf       # 1 = info, 2 = debug, 3 = trace
nextflow -log my-run.log run main.nf    # write log to a specific file

# Bigger head-process JVM heap for big pipelines
export NXF_OPTS='-Xms2g -Xmx16g'
```

Open `report.html` after a run: per-task CPU usage, peak RSS, runtime, IO. Tasks that ran at 5% CPU for hours are your refactor targets.

## 6. `-resume` cache forensics

When `-resume` re-runs a task you didn't change, find why:

```bash
# Side-by-side hash + status of every task across two runs
nextflow log gloomy_pasteur  -f hash,name,status > before.txt
nextflow log naughty_galileo -f hash,name,status > after.txt
diff before.txt after.txt
```

The hash is computed from (see `nextflow/docs/cache-and-resume.md`):

1. Task name
2. Container image (tag or digest)
3. Conda / Spack / module spec (if any)
4. Task input file contents (path + size + mtime, or content hash if `cache: 'deep'`)
5. The script text after variable substitution
6. Any global variables referenced in the script
7. `task.ext.*` properties referenced in the script (since 23.10)
8. Bundled `bin/` scripts used in the script
9. Whether `-stub-run` was used

Anything in that list changes ⇒ cache miss. Most common culprits:

| Cache miss cause | Fix |
|---|---|
| Container tag floated (`:latest`) | Pin to a digest: `container 'tool@sha256:abc...'` |
| File mtime drifted on NFS/Lustre | `process.cache = 'lenient'` (hashes size + path only) |
| Param string interpolated into script | Move the param into a tracked input, or use `cache 'deep'` |
| `task.ext.args` changed | Expected — that's the design |
| Switched between `-stub-run` and real run | Expected — separate caches by design |

`cache: 'deep'` (content hash, slow on big files) or `cache: 'lenient'` (size + path only, fast but less safe) are the two escape hatches.

## 7. Common authoring failure patterns

| Symptom | Diagnosis | Fix |
|---|---|---|
| Process never starts; downstream of a `Channel.fromPath` | Glob matched 0 files | Add `.ifEmpty { error "no files at ${params.input}" }`; check the pattern with `ls` |
| Only one sample gets the reference, rest hang | Used a queue channel where you need a value channel | Append `.first()` (see §10.6 of tricks file) |
| Process succeeds but `process.out` is empty | `output:` glob doesn't match what the script produced | `cd work/.../`, `ls`, fix the glob (quote it!) |
| `.join()` returns an empty channel | Different meta in the two channels | `.view()` both before the join; ensure first element is identical |
| `groupTuple()` never emits | Upstream channel hasn't closed | Use `groupKey(meta, n)` to declare expected group size (§10.1 of tricks file) |
| Memory leak in head process | Big `.collect()` on file paths | Use `.collectFile()` or stream-friendly operators |
| Container can't find tool | Wrong container or `$PATH` clobbered | `cat .command.run | grep docker` to see the invocation; `docker run -it <image> bash` to inspect |
| Mysterious "Killed" message | OOM-killer on the host | Lower `process.queueSize`; ensure host has enough RAM for all parallel tasks |

## 8. Reproducing a single failed task

For a hard-to-trigger failure, isolate the task:

```bash
# 1. Re-run only that process using a tag filter or one-input pipeline
nextflow run main.nf --input single-row.csv -resume

# 2. Or shell into the work dir and iterate
cd work/ab/cdef.../
bash .command.sh                # quick iteration, no container
bash .command.run               # full container-wrapped, matches Nextflow exactly

# 3. For container issues, drop into the image
grep '^docker\|^singularity' .command.run     # find the image
docker run --rm -it -v "$PWD:/work" -w /work biocontainers/tool:1.2.3 bash
# now you have a shell in the exact env Nextflow used
```

## 9. Faster local iteration — config tricks

`dev.config` profile for fast inner-loop work:

```groovy
profiles {
    dev {
        process {
            cpus = 2; memory = 4.GB; time = 30.min
            errorStrategy = 'terminate'        // fail fast, no retries
        }
        executor.queueSize = 4                 // limit local parallelism
        docker.enabled = true
        cleanup = false                        // keep work dir for inspection
    }
}
```

```bash
nextflow run main.nf -profile dev -resume
```

For container churn (rebuilding images), pre-load:

```bash
docker pull biocontainers/tool:1.2.3       # avoid mid-run pull
export NXF_SINGULARITY_CACHEDIR=$HOME/.singularity_cache  # persistent SIF cache
```

## 10. Bisecting a regression

When yesterday's pipeline worked and today's doesn't:

```bash
nextflow log                                       # list past runs
nextflow log gloomy_pasteur -f hash,name,status > yesterday.txt
nextflow log naughty_galileo -f hash,name,status > today.txt
diff yesterday.txt today.txt | grep '^[<>]' | head
```

Look for tasks whose hash changed but whose code/inputs *shouldn't* have changed — that's where the regression hides (usually a floated container tag or a global Groovy helper that drifted).

For pipeline-script regressions (your code, not data), `git bisect` runs against the snapshot test (`nf-test test --tag pipeline`) is the right move — see [`testing.md`](testing.md).

## 11. Profiling: what's the bottleneck?

```bash
nextflow run main.nf -with-trace -with-report report.html -resume
```

Then open `report.html` and sort by:

- **% requested CPU used** — < 30% means you over-allocated; the task is IO-bound or single-threaded
- **% requested memory used** — < 30% means downsize; > 90% will OOM on retry
- **Realtime vs CPU time** — wall clock much bigger than CPU time = IO wait or container pull
- **Tasks in pending status** — queue starvation, raise `executor.queueSize`

Trace columns worth filtering on the CSV (`trace.txt`):

```bash
awk -F'\t' 'NR>1 && $5=="COMPLETED" {print $1, $4, $11, $12}' trace.txt | sort -k4 -h | tail -20
# top 20 longest tasks
```

## 12. The dev `nextflow.config` overrides everyone wants

Drop this in `~/.nextflow/config` for global authoring conveniences:

```groovy
docker.enabled = true
docker.runOptions = '-u $(id -u):$(id -g)'        // no root-owned files in work/

dag    { overwrite = true }
report { overwrite = true }
trace  { overwrite = true }

env.NXF_OPTS = '-Xms1g -Xmx8g'
env.NXF_ANSI_LOG = 'true'
```

Anything in `~/.nextflow/config` applies to every `nextflow run` you launch, before pipeline configs.
