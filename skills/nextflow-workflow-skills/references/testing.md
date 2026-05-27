# Testing Nextflow pipelines with nf-test

Patterns lifted from `nf-core/rnaseq` 3.26 and `nf-core/sarek` 3.8 — both ship 100+ nf-test files covering modules, subworkflows, and full-pipeline runs. Use nf-test instead of bespoke shell scripts; it gives you snapshots, fixtures, tags, CI integration, and runs your tests with `-stub` for free.

## 1. Install + project setup

```bash
# Install (Java required)
curl -fsSL https://code.askimed.com/install/nf-test | bash
# or with conda
conda install -c bioconda nf-test
nf-test version       # ≥ 0.9.0 recommended
```

Top-level `nf-test.config` (copy from rnaseq with edits):

```groovy
config {
    testsDir = "."                                         // tests live next to code
    workDir  = System.getenv("NFT_WORKDIR") ?: ".nf-test"  // scratch dir
    configFile = "tests/nextflow.config"                   // test-only nextflow.config

    // Skip vendored nf-core/modules tests; you didn't write them
    ignore = ['modules/nf-core/**/tests/*', 'subworkflows/nf-core/**/tests/*']

    // Re-run ALL tests when these files change (CI hook)
    triggers = ['nextflow.config', 'nf-test.config', 'conf/test.config']

    // Custom assertion plugins
    plugins {
        load "nft-bam@0.4.0"        // assertBam(...) for BAM files
        load "nft-utils@0.0.5"      // getAllFilesFromDir + path normalisation
    }
}
```

Test-only config (`tests/nextflow.config`) typically shrinks resources so CI doesn't melt:

```groovy
process {
    cpus = 2; memory = 6.GB; time = 1.h
    resourceLimits = [cpus: 4, memory: 8.GB, time: 2.h]
}
```

## 2. Three test scopes — one DSL each

### Module test (a single process)

`modules/local/fastqc/tests/main.nf.test`:

```groovy
nextflow_process {
    name "Test FASTQC"
    script "../main.nf"
    process "FASTQC"
    tag "modules"
    tag "fastqc"

    test("single-end") {
        when {
            process {
                """
                input[0] = [ [id: 'sample1', single_end: true],
                             file(params.test_data['test_1_fastq_gz'], checkIfExists: true) ]
                """
            }
        }
        then {
            assertAll(
                { assert process.success },
                { assert path(process.out.zip[0][1]).exists() },
                { assert snapshot(process.out.versions).match("versions") }
            )
        }
    }
}
```

### Subworkflow test

`subworkflows/local/align_and_count/tests/main.nf.test`:

```groovy
nextflow_workflow {
    name "Test ALIGN_AND_COUNT"
    script "../main.nf"
    workflow "ALIGN_AND_COUNT"
    tag "subworkflows"

    test("paired-end + GTF") {
        when {
            workflow {
                """
                input[0] = Channel.of([ [id: 'sample1'],
                                        [file('${projectDir}/tests/data/R1.fq.gz'),
                                         file('${projectDir}/tests/data/R2.fq.gz')] ])
                input[1] = file('${projectDir}/tests/data/star_index/')
                input[2] = file('${projectDir}/tests/data/annotation.gtf')
                """
            }
        }
        then {
            assertAll(
                { assert workflow.success },
                { assert snapshot(workflow.out.counts).match("counts") }
            )
        }
    }
}
```

### Pipeline test (end-to-end)

`tests/skip_qc.nf.test` (verbatim pattern from rnaseq):

```groovy
nextflow_pipeline {
    name "Test pipeline by skipping QC options"
    script "../main.nf"
    tag "pipeline"

    test("Params: --skip_qc --min_mapped_reads 90") {
        when {
            params {
                outdir = "$outputDir"
                skip_qc = true
                min_mapped_reads = 90
            }
        }
        then {
            // Stable name: relative paths of every published file/dir
            def stable_name = getAllFilesFromDir(
                params.outdir, relative: true, includeDir: true,
                ignore: ['pipeline_info/*.{html,json,txt}']        // timestamps churn
            )
            // Stable content: full content hash of every reproducible file
            def stable_path = getAllFilesFromDir(
                params.outdir, ignoreFile: 'tests/.nftignore'
            )
            assertAll(
                { assert workflow.success },
                { assert snapshot(
                    workflow.trace.succeeded().size(),              // # tasks succeeded
                    removeFromYamlMap("$outputDir/.../versions.yml", "Workflow"),
                    stable_name,
                    stable_path
                ).match() }
            )
        }
    }

    test("same params - stub") {
        options "-stub"                          // mirror the test under -stub-run
        when { params { outdir = "$outputDir"; skip_qc = true } }
        then { /* same assertions */ }
    }
}
```

## 3. The two assertions that matter

Every pipeline test in rnaseq/sarek boils down to:

1. **`stable_name`** — set of all output paths. Catches "did we accidentally stop publishing X?".
2. **`stable_path`** — content hashes of all outputs. Catches "did X's content change?".

Filter out files that are inherently non-reproducible (HTML reports with timestamps, log files, `versions.yml` Workflow blocks):

```groovy
def stable_name = getAllFilesFromDir(
    params.outdir, relative: true, includeDir: true,
    ignore: ['pipeline_info/*.{html,json,txt}', '*.log']
)
```

`tests/.nftignore` lists glob patterns of files whose **content** is volatile (gzip mtimes, randomly-seeded outputs):

```
**/*.bai
**/multiqc_report.html
**/pipeline_info/*.{html,json,txt,yml}
```

## 4. Snapshot lifecycle

```bash
nf-test test tests/skip_qc.nf.test                # first run: creates .snap file
nf-test test --update-snapshot tests/skip_qc.nf.test  # accept new outputs after intentional change
nf-test test --clean                              # nuke .nf-test/ scratch
```

Snapshot files live next to tests as `<test>.nf.test.snap` (JSON). **Commit them.** Diffs in PRs are how you review pipeline output changes.

## 5. Running selectively

```bash
nf-test test                              # all tests under testsDir
nf-test test --tag fastqc                 # only tests tagged "fastqc"
nf-test test --tag pipeline --profile=+test,docker
nf-test test --only-changes               # only tests touching changed files (CI sweet spot)
nf-test test --debug                      # verbose; prints the underlying nextflow command
```

Tags compose: `tag "modules"; tag "fastqc"; tag "single-end"` lets CI shard by `--tag modules` (fast) vs `--tag pipeline` (slow).

## 6. Test fixtures

Two strategies, both used by nf-core:

- **Tiny inputs in-repo** (`tests/data/*.fq.gz`, < 100 KB each) — fast, deterministic, but bloats the repo.
- **`params.test_data` map** loading from `nf-core/test-datasets` GitHub repo via raw URLs — keeps repo small. Pattern:

  ```groovy
  // conf/test.config
  params {
      test_data_base = 'https://raw.githubusercontent.com/nf-core/test-datasets/rnaseq'
      input = "${params.test_data_base}/samplesheet/v3.10/samplesheet_test.csv"
  }
  ```

For your own pipelines, **start with in-repo fixtures**. Move to a `test-datasets` branch only when CI download cost matters.

## 7. CI wiring (GitHub Actions, nf-core template)

```yaml
# .github/workflows/nf-test.yml
on: [pull_request]
jobs:
  test:
    strategy:
      matrix:
        profile: [docker, singularity, conda]
        tag:    [pipeline, modules]
    steps:
      - uses: actions/checkout@v4
      - uses: nf-core/setup-nextflow@v2
      - run: |
          curl -fsSL https://code.askimed.com/install/nf-test | bash
          ./nf-test test --tag ${{ matrix.tag }} \
                         --profile=+test,${{ matrix.profile }} \
                         --only-changes
```

`--only-changes` + the `triggers` block in `nf-test.config` together mean: PRs touching a single module re-run only that module's tests; PRs touching `nextflow.config` re-run everything.

## 8. Idiomatic assertions cheat-sheet

```groovy
// File existence + content
assert path(process.out.bam[0][1]).exists()
assert path(process.out.csv[0][1]).text.contains("sample,count")
assert path(...).readLines().size() > 100
assert path(...).md5 == "d41d8cd98f00b204e9800998ecf8427e"

// BAM-specific (nft-bam plugin)
assert bam(process.out.bam[0][1]).getReadsMD5() == "..."   // ignores header timestamps

// Channel contents (no snapshot — exact match)
assert process.out.counts.size() == 3
assert process.out.versions.size() == 1

// Workflow-level
assert workflow.success
assert workflow.failed
assert workflow.trace.succeeded().size() == 42
assert workflow.trace.tasks().any { it.name.contains("STAR_ALIGN") }
```

## 9. Common pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| Snapshot diffs on every run | Including non-reproducible files (HTML, gzip mtimes) | Add to `ignore:` list or `.nftignore` |
| Test passes locally, fails in CI | Different container engine or resource limits | Pin both with `--profile=+test,docker`; mirror CI config locally |
| `-stub` test snapshots clash with real-run snapshots | Same `outputDir` shared | Use separate snapshot files or different test names |
| Test runs forever | Real data instead of stub | Add a paired `options "-stub"` test for fast iteration |
| `getAllFilesFromDir` returns absolute paths | Forgot `relative: true` | Always pass `relative: true` for portable snapshots |

## References

- nf-test docs — https://www.nf-test.com
- `nft-utils` plugin — https://github.com/nf-core/nft-utils
- `nft-bam` plugin — https://github.com/nf-core/nft-bam
- rnaseq tests directory — `rnaseq/tests/` (local) for working examples
- sarek tests directory — `sarek/tests/` (local) for tumor/normal test patterns
