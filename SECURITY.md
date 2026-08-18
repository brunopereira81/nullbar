# Security

## Reporting

Please report vulnerabilities through GitHub's private advisory flow on
[this repository](https://github.com/brunopereira81/nullbar/security/advisories/new)
rather than a public issue.

## Threat model, stated plainly

`nullbar` is tamper-**evident**, not tamper-proof, and only against
accident and self-deception — not against a motivated adversary who
controls the filesystem.

- `Registration.freeze()` hashes the design and bar; `verdict()` refuses to
  grade when the file on disk and the object in memory disagree; the
  test-look stamp records the registration's sha256.
- All of that lives in ordinary files owned by the researcher. Deleting the
  registration and the stamp and starting over defeats every guarantee here,
  and nothing in this package can detect it.

If a third party has to believe the record — an allocator, an auditor, a
compliance file — anchor it somewhere the researcher does not control: a
signed git commit, an RFC-3161 timestamp, a transparency log, or a
counterparty's own store. The hashes this library produces are designed to
be what you anchor.

`lint_source()` and `prefix_replay_check()` read and execute the code and
callables you point them at. Do not point them at untrusted sources.
