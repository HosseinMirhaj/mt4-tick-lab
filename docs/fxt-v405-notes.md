# FXT v405 / HST v401 — offline notes

Findings from building and testing an offline MT4 Build 1470 test bed.

## Verified facts

* `HST1` = 148-byte header + N × 60-byte records.
  Header `struct.Struct("<i64s12siiii13i")`, record `struct.Struct("<qddddqiq")`.
  Record order follows `MqlRates`: time, **open, high, low, close**, tick_volume, spread, real_volume.
* `FXT` = 728-byte header + N × 56-byte records.
  Record `struct.Struct("<qddddqii")` = (bar_time, open, high, low, close, tick_volume, tick_time, flag).
  A model-0 (every tick) FXT stores one record per tick, not one per bar.
* The FXT header carries the **bar** count at offset 216, not the record count.
  A stock MT4 FXT with 92,132,168 tick records reported `bars = 353451`.
* **The trailing int of the header block at offset 208 is 0 in MT4's own files.**
  Same reference file, ~92M tick records, still 0. It is not a tick counter and must be written as 0.
* MT4 resolves a tester symbol's M1 history from `history\<server>\<SYMBOL>1.hst`;
  symbol names differ per broker, so `XAUUSD` on one server is `GOLD` on another.
  `--symbol` on the exporters renames the artifact instead of copying broker databases.

## Symptom log (unresolved)

Running the tester on a hand-built pilot produced, in order:

```
TestGenerator: file "...\GOLD1_0.fxt" cannot open [5]   (after chmod +R)
TestGenerator: no history data 'GOLD1' from 2026.09.14 to 2026.09.16
TestGenerator: deficient data 'GOLD1' (91 rate records)
```

After the last run `tester\history\GOLD1_0.fxt` was **0 bytes**: MT4 truncated the
hand-built FXT and tried to rebuild it from M1 history, then stopped.
The tester-only EA never ran, so no result file was produced.

## Root cause (confirmed)

**MT4 refuses to start a test with fewer than 100 bars of M1 history.**
The number in the message is the bar count it found, so `(91 rate records)` means
91 of the required 100. Our pilot covers 2026-09-14 18:34 → 20:43 = 91 M1 bars.
MT4 read `GOLD1.hst` and reported its exact bar count. It then truncated
`GOLD1_0.fxt` to 0 bytes before the tester-only EA could run, so no result CSV
appeared. This failure confirms the HST is found, **not** that the FXT layout has
been accepted; the FXT still requires a separate successful smoke test.

Source: <https://www.mql5.com/en/articles/1417>

The tester's date fields accept a **date only** — no time of day — so narrowing the
range cannot work around the 100-bar minimum.

### Fix

Collect a longer continuous tick session. 100 bars is the hard floor; MT4 also wants
bars *preceding* the start date for indicator warm-up, so aim well above it.
A single uninterrupted session of ~4 hours yields ~240 M1 bars and clears the
threshold with margin. No synthetic or padded bars — the project requires that every
bar come from real broker ticks.

## Safety

HST/FXT are produced only for the offline copy under `C:\MT4-HST_Offline`.
Nothing is ever written into a live terminal's `history` or `tester` folders.
