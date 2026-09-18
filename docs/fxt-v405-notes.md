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

## Symptom log

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
91 of the required 100. Our pilot covered 2026-09-14 18:34 → 20:43 = 91 M1 bars.
MT4 read `GOLD1.hst` and reported its exact bar count, then truncated
`GOLD1_0.fxt` to 0 bytes before the tester-only EA could run.

Source: <https://www.mql5.com/en/articles/1417>

The tester's date fields accept a **date only** — no time of day — so narrowing the
range cannot work around the 100-bar minimum.

### Fix

Collect a longer continuous tick session. 100 bars is the hard floor; MT4 also wants
bars *preceding* the start date for indicator warm-up, so aim well above it.
A single uninterrupted session of several hours clears the threshold with margin.
No synthetic or padded bars — every bar must come from real broker ticks.

## First successful smoke test (2026-09-18)

270 real M1 bars over 32,860 ticks cleared the threshold and the tester ran:

```
TestGenerator: spread set to 11
TestGenerator: no connect to trade server, default environment will be applied
GOLD,M1: 20577 tick events (170 bars, 20677 bar states) processed
MT4 Tick Lab FXT smoke result: ticks=20577 first=2026.09.18 10:55:00 orders=0
```

The EA ran on real ticks and placed no orders. Confirmed from this run:

* The FXT layout is accepted; the header's fixed spread reaches the tester.
* A FXT tick record's inner time field is **4 bytes, not 8**. Writing 8 bytes makes
  MT4 read a truncated value, which is why the EA reported `last=1993.06.22` while
  the 8-byte **bar** time stayed correct. Only the bar time drives the test range.
* The tick count the tester processed (20,577) is lower than the file's record count
  (32,860) because only the part of the range covered by the imported history is fed
  to the EA. Match the tester date range to the data before judging coverage.
* The offline terminal runs at GMT+3, so broker wall-clock times near the end of a
  broker day land on the next calendar day. This affects the range to select.

### Open issue

`build_fxt.py` still writes an 8-byte inner tick time. The test passes, but the field
is malformed and must be narrowed to 4 bytes before any result that depends on
intra-bar timing is trusted.

## Other facts worth keeping

* The official FXT documentation lists record OHLC as **open, low, high, close**,
  but reverse-engineered layouts and this working test both show the real byte order
  is **open, high, low, close**. Follow the bytes, not the doc.
* MT4 ignores extra copies of `<SYMBOL>1.hst` outside the active server folder. When
  the active server folder is unknown, distribute the HST to every folder under
  `history\` rather than guessing.
* An offline terminal launched with `/portable` still reports
  `no connect to trade server, default environment will be applied`; that is expected
  and does not block a test.

Sources: <https://www.metatrader4.com/en/trading-platform/help/autotrading/tester/tester_fxt>,
<https://github.com/adyzng/go-duka>, <https://github.com/EA31337/MT-Formats>

## Safety

HST/FXT are produced only for the offline copy under `C:\MT4-HST_Offline`.
Nothing is ever written into a live terminal's `history` or `tester` folders.
