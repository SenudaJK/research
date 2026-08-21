# Experiment Log

Record each chaos engineering run for reproducibility and thesis evidence.

## Template

```markdown
## Scenario {N} - {Name} - Run {A|B} - Iteration {1-10}

- **Date:**
- **T0 fault start (UTC):**
- **Td detection (UTC):**
- **Tr recovery (UTC):**
- **Te trial end (UTC):**
- **MTTD (Td − T0):**
- **MTTR (Tr − T0):**
- **Availability (frontend success spans / all frontend server spans):**
- **Detection outcome:** TP / FP / FN / TN
- **Run A signal (if A):** success-rate | pod-not-ready | restart-count
- **Run B score / τ (if B):**
- **Rule matched (if B):**
- **Action taken (if B):** dry-run | restart | scale | evict | none
- **Censored:** no / MTTD / MTTR
- **Notes:**
```

Definitions: `docs/measurement-protocol.md`.

---

## Runs

<!-- Add entries below as experiments are executed -->
