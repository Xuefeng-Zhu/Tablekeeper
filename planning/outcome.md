# Tablekeeper run-4 outcome: BLOCKED

Stage 1 was rejected at `7edd47cccf839e8ec175c1e7e7252f40d80cf191`. No stage is accepted. Stages 2–4 were not started because the stage-1 release gate failed.

Independent review found S1-DEFECT-03: valid UTC dates `0001-01-01` and `0999-01-07` return 422 from availability and reservation creation instead of 200 and 201. Linux year formatting omits required leading zeros in internal date strings. This violates stage-1 §§4 and 8. All three permitted repair cycles are exhausted; no fourth repair is authorized.

The final independent isolated suite passed 120/120, QA scenarios passed 8/8, both earlier defects were independently repaired, and numeric identity, portability and 17 unit tests passed within their observed scope. Those results do not waive the calendar failure.

The rejected product remains unchanged. The final PM record commit changes coordination records only and does not constitute a new product candidate or acceptance. See [outcome.json](outcome.json) for exact revision, room receipt, evidence hashes and limitations; [requirements-coverage.csv](requirements-coverage.csv) retains the full acceptance requirements and scoped reviewer observations.

Final reviewer evidence is under `.evidence/S1-REVIEW2-20261004T200934Z/`, including `candidate-acceptance.json`, `requirement-map.json`, `calendar_range.py` and `calendar-check.stdout`. Failed prior evidence is retained. The stage-1 directory is a rejected implementation checkpoint, not a completed release.

Final human README/FACTORY narratives, actual complete post-run room export and final packaging remain uncompleted and NOT_TESTED. Hidden judgment and exhaustive input/concurrency coverage are not claimed. Usage and cost are UNAVAILABLE. Implementation stops under the current limits; future repair requires separately authorized scope/budget and a new independent gate.
