import os
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles
from cocotb.handle import Force, Release

def get_core(dut):
    """Finds the core tt_um_authQV instance."""
    if hasattr(dut, "user_project"): return dut.user_project
    else: raise AttributeError("Could not find the tt_um_authQV instance.")

def get_mem_ctrl(core):
    """Finds the tinyqv_mem_ctrl instance."""
    if hasattr(core, "i_mem"): return core # In case q_ctrl is instantiated directly
    else: raise AttributeError("Could not find the mem_ctrl instance.")

async def sync_to_phase(dut, core, target_phase):
    """Waits until the internal bridge phase counter matches the target."""
    while True:
        # DRIVE SAFE IDLE: 0x70 prevents accidental reads and fetches while waiting
        dut.ui_in.value = 0x70 
        await RisingEdge(dut.clk)
        if core.i_bridge.phase.value.to_unsigned() == target_phase:
            break

async def send_bridge_request(dut, core, fetch_restart=0, fetch_stall=0, instr_addr=0, data_addr=0, data_read_n=3):
    """Serializes a CPU memory request across the 8-phase parallel bus (ui_in)."""
    await sync_to_phase(dut, core, 7)
    
    dut.ui_in.value = (fetch_restart << 7) | (fetch_stall << 6) | ((instr_addr >> 18) & 0x3F)
    await RisingEdge(dut.clk) # Phase 0
    dut.ui_in.value = (instr_addr >> 10) & 0xFF
    await RisingEdge(dut.clk) # Phase 1
    dut.ui_in.value = (instr_addr >> 2) & 0xFF
    await RisingEdge(dut.clk) # Phase 2
    dut.ui_in.value = (((instr_addr >> 1) & 0x01) << 7) | (((data_addr >> 18) & 0x3F) << 1)
    await RisingEdge(dut.clk) # Phase 3
    dut.ui_in.value = (data_addr >> 10) & 0xFF
    await RisingEdge(dut.clk) # Phase 4
    dut.ui_in.value = (data_addr >> 2) & 0xFF
    await RisingEdge(dut.clk) # Phase 5
    dut.ui_in.value = ((data_addr & 0x03) << 6) | ((data_read_n & 0x03) << 4)
    await RisingEdge(dut.clk) # Phase 6
    
    # Phase 7 is a spare phase. Use it to return the bus to the safe IDLE state.
    dut.ui_in.value = 0x70
    await RisingEdge(dut.clk) # Phase 7

async def setup_dut(dut):
    """Standard initialization and reset sequence."""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    dut.rst_n.value = 0
    dut.ena.value = 0
    dut.ui_in.value = 0x70 # Initialize in a safe IDLE state instead of 0
    dut.uio_in.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    dut.ena.value = 1
    await ClockCycles(dut.clk, 2)
    return get_core(dut)

async def monitor_qspi_tx(dut, mem_ctrl):
    """Monitors the SPI pins and captures the address/mode bits sent to the flash."""
    captured_nibbles = []
    
    while int(mem_ctrl.qspi_flash_select.value) == 1:
        await RisingEdge(dut.clk)
        
    prev_clk = int(mem_ctrl.qspi_clk_out.value)
    
    while int(mem_ctrl.qspi_flash_select.value) == 0:
        await RisingEdge(dut.clk)
        curr_clk = int(mem_ctrl.qspi_clk_out.value)
        
        if prev_clk == 0 and curr_clk == 1:
            if mem_ctrl.qspi_data_oe.value.to_unsigned() > 0:
                nibble = mem_ctrl.qspi_data_out.value.to_unsigned()
                captured_nibbles.append(nibble)
            else:
                break
        prev_clk = curr_clk
        
    return captured_nibbles

# ==============================================================================
# TEST 1: The Request Deserializer (FPGA -> Bridge)
# ==============================================================================
@cocotb.test(skip=os.environ.get("GATES") == "yes")
async def test_bridge_deserialization(dut):
    core = await setup_dut(dut)
    test_instr_addr = 0x5A5A5A 
    test_data_addr = 0xBCDEFA  
    
    await send_bridge_request(dut, core, 1, 1, test_instr_addr, test_data_addr, 1) 
    
    assert core.i_bridge.instr_addr.value.to_unsigned() == (test_instr_addr >> 1), "Failed to reconstruct instr_addr"
    assert core.i_bridge.data_addr.value.to_unsigned() == test_data_addr, "Failed to reconstruct data_addr"
    assert core.i_bridge.data_read_n.value.to_unsigned() == 1, "Failed to reconstruct data_read_n"
    dut._log.info("SUCCESS: All persistent addresses and commands reconstructed perfectly.")

# ==============================================================================
# TEST 2: The Status Catcher (Memory Controller -> Bridge Latches)
# ==============================================================================
@cocotb.test(skip=os.environ.get("GATES") == "yes")
async def test_bridge_pulse_latching(dut):
    core = await setup_dut(dut)
    await sync_to_phase(dut, core, 2)
    
    core.i_bridge.instr_fetch_started.value = Force(1)
    core.i_bridge.data_ready.value = Force(1)
    core.i_bridge.data_from_read.value = Force(0xDEADBEEF)
    await RisingEdge(dut.clk)
    
    core.i_bridge.instr_fetch_started.value = Release()
    core.i_bridge.data_ready.value = Release()
    core.i_bridge.data_from_read.value = Release()
    
    await sync_to_phase(dut, core, 6)
    
    assert int(core.i_bridge.started_l.value) == 1, "Failed to latch instr_fetch_started pulse."
    assert int(core.i_bridge.data_ready_l.value) == 1, "Failed to latch data_ready pulse."
    assert core.i_bridge.data_from_read_l.value.to_unsigned() == 0xDEADBEEF, "Failed to latch data_from_read."
    dut._log.info("SUCCESS: 1-cycle pulses successfully caught and latched.")

# ==============================================================================
# TEST 3: The Response Serializer (Bridge -> FPGA)
# ==============================================================================
@cocotb.test(skip=os.environ.get("GATES") == "yes")
async def test_bridge_serialization(dut):
    core = await setup_dut(dut)
    await sync_to_phase(dut, core, 6)
    
    core.i_bridge.started_l.value = Force(1)
    core.i_bridge.data_ready_l.value = Force(1)
    core.i_bridge.instr_data_l.value = Force(0xCAFE)
    core.i_bridge.data_from_read_l.value = Force(0x12345678)
    
    await sync_to_phase(dut, core, 0) 
    
    expected_status = (1 << 7) | (0 << 6) | (0 << 5) | (1 << 4)
    assert dut.uo_out.value.to_unsigned() == expected_status, "Failed to output status byte"
    await RisingEdge(dut.clk) 
    assert dut.uo_out.value.to_unsigned() == 0xFE
    await RisingEdge(dut.clk) 
    assert dut.uo_out.value.to_unsigned() == 0xCA
    await RisingEdge(dut.clk) 
    assert dut.uo_out.value.to_unsigned() == 0x78
    await RisingEdge(dut.clk) 
    assert dut.uo_out.value.to_unsigned() == 0x56
    await RisingEdge(dut.clk) 
    assert dut.uo_out.value.to_unsigned() == 0x34
    await RisingEdge(dut.clk) 
    assert dut.uo_out.value.to_unsigned() == 0x12
    await RisingEdge(dut.clk) 
    assert dut.uo_out.value.to_unsigned() == 0x00

    core.i_bridge.started_l.value = Release()
    core.i_bridge.data_ready_l.value = Release()
    core.i_bridge.instr_data_l.value = Release()
    core.i_bridge.data_from_read_l.value = Release()
    dut._log.info("SUCCESS: All phases serialized and output correctly.")

# ==============================================================================
# TEST 4: Flash Protocol Validation (ASIC -> Flash)
# ==============================================================================
@cocotb.test(skip=os.environ.get("GATES") == "yes")
async def test_qspi_tx_protocol(dut):
    """Validates that the exact, correct nibbles are driven on the SPI clock edges."""
    core = await setup_dut(dut)
    mem_ctrl = get_mem_ctrl(core)
    
    test_data_addr = 0xABCDEF
    
    # 1. Start the SPI monitor in the background
    monitor_task = cocotb.start_soon(monitor_qspi_tx(dut, mem_ctrl))
    
    # 2. Trigger the read. (fetch_stall=1 keeps the instruction side quiet)
    await send_bridge_request(dut, core, fetch_restart=0, fetch_stall=1, instr_addr=0, data_addr=test_data_addr, data_read_n=0)
    
    # 3. Wait for the monitor to finish capturing the transmission
    captured_nibbles = await monitor_task
    
    # 4. Verify the protocol 
    expected_address = [0xA, 0xB, 0xC, 0xD, 0xE, 0xF]
    
    assert len(captured_nibbles) >= 6, f"Did not capture enough nibbles. Got: {captured_nibbles}"
    assert captured_nibbles[0:6] == expected_address, f"Address mismatch. Expected {expected_address}, got {captured_nibbles[0:6]}"
    
    if len(captured_nibbles) >= 8:
        assert captured_nibbles[6:8] == [0xA, 0xA], "Mode bits did not match expected 4'b1010"
        
    dut._log.info(f"SUCCESS: Captured exact QSPI TX sequence: {[hex(n) for n in captured_nibbles]}")