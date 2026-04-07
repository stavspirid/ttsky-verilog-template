# Minimal CPU — 1x1 Tile Strip-Down

Summary of the changes made to fit the authQV RISC-V core into a single
TinyTapeout tile (~167×108 µm). Everything not strictly required for an
RV32I core + QSPI memory interface was removed; those features are
expected to live on the companion FPGA instead.

## Result

| Metric              | Before | After  | Change   |
| ------------------- | ------ | ------ | -------- |
| Generic cells (yosys `synth`) | 4103   | 3541   | **−14%** |
| Estimated utilization (1x1)   | ~44%   | ~37%   | −7 pts   |
| Synthesis warnings  | 1 benign | 1 benign | — |

The single remaining warning is the expected register-file memory inference
in `register.v`.

## Modules kept

| File          | Role                                          |
| ------------- | --------------------------------------------- |
| `project.v`   | Top-level wrapper, GPIO, peripheral mux       |
| `tinyqv.v`    | CPU + memory controller wrapper               |
| `cpu.v`       | Instruction fetch, decode latch, sequencing   |
| `core.v`      | ALU execution, writeback, traps, CSRs         |
| `decode.v`    | RV32I instruction decoder                     |
| `alu.v`       | ALU + barrel shifter                          |
| `register.v`  | 16-entry × 32-bit register file (4-bit serial)|
| `mem_ctrl.v`  | Flash/RAM transaction orchestration           |
| `qspi_ctrl.v` | QSPI protocol FSM                             |

`counter.v` was deleted entirely.

## What was removed and why

### `alu.v` — multiplier dropped
`tinyqv_mul` performed a 4×16-bit parallel multiply per clock with a 16-bit
accumulator. It was the largest single optional block. MUL/MULH are no
longer implemented.

### `decode.v` — trimmed to standard RV32I
- Custom multi-load / multi-store extension removed
  (`instr[13:12]==2'b11` block).
- Custom fast memset extension removed (`SW` with `instr[14:12]==3'b110`).
- MUL / Zicond branch in the `alu_op` encoder removed.
- Dead commented-out compressed-instruction (`-c` extension) code purged.
- `additional_mem_ops` and `mem_op_increment_reg` outputs deleted.

### `core.v` — biggest reduction
- Both `tinyqv_counter` instances removed (`mcycle`, `mtime`, `minstret`
  no longer implemented).
- `tinyqv_mul` instantiation and the `is_mul` writeback path removed.
- Zicond (`CZERO.eqz/nez`) `is_czero` path removed.
- External interrupt machinery removed: `mie`, `mip_reg`,
  `last_interrupt_req`, `interrupt_pending`, `interrupt_req`,
  `timer_interrupt`, the `is_interrupt` input.
- CSR read/write logic trimmed to:
  `mstatus` (0x300), `misa` (0x301), `mepc` (0x341),
  `mcause` (0x342), `mimpid` (0xF13).
- ECALL / EBREAK / MRET trap handling kept intact (with the non-standard
  `mstatus.mte` double-fault guard).
- All debug output ports removed.

### `cpu.v`
- Interrupt plumbing (`interrupt_core`, `interrupt_pending`,
  `interrupt_req`, `timer_interrupt`) removed.
- Custom multi-op handling removed: `additional_mem_ops`,
  `mem_op_increment_reg`, `addr_offset`, `any_additional_mem_ops`.
- `data_addr` is now driven directly from `addr_out` (no nibble offset
  windowing for multi-op instructions).
- All `debug_*` outputs removed.

### `mem_ctrl.v`
- `debug_stall_txn` and `debug_stop_txn` outputs removed.

### `tinyqv.v`
- All `debug_*` ports stripped.
- `interrupt_req` and `timer_interrupt` ports removed.

### `project.v` — top-level simplification
- Removed the 16-entry debug-signal multiplexer feeding `uo[7]`.
- Removed `gpio_out_sel` register; `uo_out` is now wired directly to
  `gpio_out`.
- Removed `debug_register_data` / `debug_rd_r` registers.
- Removed `PERI_DEBUG` and `PERI_GPIO_OUT_SEL` peripherals.
- Removed `ui_in` rising-edge interrupt detection — interrupts are no
  longer routed into the CPU.
- `tinyQV` instantiation has no debug or interrupt connections.

### Build files
- `info.yaml`: removed `counter.v` from `source_files`.
- `test/Makefile`: removed `counter.v` from `PROJECT_SOURCES`.

## Resulting ISA / programmer-visible interface

**Supported instructions**

- Arithmetic / logic: `ADD(I)`, `SUB`, `AND(I)`, `OR(I)`, `XOR(I)`,
  `SLT(I)(U)`
- Shifts: `SLL(I)`, `SRL(I)`, `SRA(I)`
- Upper-immediate: `LUI`, `AUIPC`
- Control flow: `JAL`, `JALR`, `BEQ`, `BNE`, `BLT(U)`, `BGE(U)`
- Memory: `LB`, `LH`, `LW`, `LBU`, `LHU`, `SB`, `SH`, `SW`
- System: `ECALL`, `EBREAK`, `MRET`, CSR read/write/set/clear

**No longer supported**

- `MUL`, `MULH*` — fault on illegal instruction.
- `CZERO.eqz`, `CZERO.nez` (Zicond) — fault on illegal instruction.
- The non-standard multi-load/store and fast-memset encodings.
- External interrupts and timer interrupt.
- Performance counters (`mcycle`, `mtime`, `minstret`).

**Memory map (unchanged)**

| Range                       | Region                |
| --------------------------- | --------------------- |
| `0x0000_0000`–`0x00FF_FFFF` | QSPI flash (read)     |
| `0x0100_0000`–`0x017F_FFFF` | QSPI RAM A            |
| `0x0180_0000`–`0x01FF_FFFF` | QSPI RAM B            |
| `0x8000_0000` (peri 0)      | GPIO output register  |
| `0x8000_0004` (peri 1)      | GPIO input (`ui_in`)  |

## What lives on the FPGA now

- Hardware multiplier (or do MUL in software)
- Cycle/time/instruction counters
- Interrupt controller and timer
- Any debug observation logic
- Any extra peripherals beyond simple GPIO

The CPU↔FPGA interface is the 8-bit GPIO out / 8-bit `ui_in` plus the
shared QSPI bus.

## Verification status

- `yosys -p "synth -top tt_um_authQV"` completes cleanly.
- Generic cell count: 3541 (vs 4103 baseline).
- Full TinyTapeout LibreLane flow not yet re-run after the strip-down —
  next step is to invoke the project hardening pipeline and confirm
  placement, routing, DRC and LVS still pass.
