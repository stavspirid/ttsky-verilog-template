import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

class FpgaMemBridge:
    def __init__(self, dut):
        self.dut = dut
        self.clk = dut.clk
        self.phase = 1
        
        self.req_instr_restart = 0
        self.req_instr_stall = 0
        self.req_instr_addr = 0
        self.req_data_addr = 0
        self.req_data_read_n = 3
        
        self.resp_started = 0
        self.resp_stopped = 0
        self.resp_instr_ready = 0
        self.resp_data_ready = 0
        self.resp_instr_data = 0
        self.resp_data_from_read = 0
        
        self._coro = cocotb.start_soon(self._run())
        
    async def _run(self):
        while True:
            # 1. Drive ui_in for current phase
            if self.phase == 7:
                val = (self.req_instr_restart << 7) | (self.req_instr_stall << 6) | ((self.req_instr_addr >> 18) & 0x3F)
            elif self.phase == 0:
                val = (self.req_instr_addr >> 10) & 0xFF
            elif self.phase == 1:
                val = (self.req_instr_addr >> 2) & 0xFF
            elif self.phase == 2:
                val = (((self.req_instr_addr >> 1) & 0x01) << 7) | (((self.req_data_addr >> 18) & 0x3F) << 1)
            elif self.phase == 3:
                val = (self.req_data_addr >> 10) & 0xFF
            elif self.phase == 4:
                val = (self.req_data_addr >> 2) & 0xFF
            elif self.phase == 5:
                val = ((self.req_data_addr & 0x03) << 6) | ((self.req_data_read_n & 0x03) << 4)
            else:
                val = 0x70 # Safe idle state for phase 6, driving phase 7 idle data

            self.dut.ui_in.value = val

            # Wait for clock edge
            await RisingEdge(self.clk)
            
            # 2. Read uo_out (it holds the value assigned at the edge that just happened)
            try:
                uo_out = self.dut.uo_out.value.to_unsigned()
            except ValueError:
                uo_out = 0 # Handle 'x' or 'z'
                
            if self.phase == 7: # We were in phase 7, edge was 7->0
                self.resp_started = (uo_out >> 7) & 1
                self.resp_stopped = (uo_out >> 6) & 1
                self.resp_instr_ready = (uo_out >> 5) & 1
                self.resp_data_ready = (uo_out >> 4) & 1
            elif self.phase == 0: 
                self.resp_instr_data = (self.resp_instr_data & 0xFF00) | uo_out
            elif self.phase == 1: 
                self.resp_instr_data = (uo_out << 8) | (self.resp_instr_data & 0x00FF)
            elif self.phase == 2:
                self.resp_data_from_read = (self.resp_data_from_read & 0xFFFFFF00) | uo_out
            elif self.phase == 3:
                self.resp_data_from_read = (self.resp_data_from_read & 0xFFFF00FF) | (uo_out << 8)
            elif self.phase == 4:
                self.resp_data_from_read = (self.resp_data_from_read & 0xFF00FFFF) | (uo_out << 16)
            elif self.phase == 5:
                self.resp_data_from_read = (self.resp_data_from_read & 0x00FFFFFF) | (uo_out << 24)

            self.phase = (self.phase + 1) % 8

    async def request(self, instr_addr=0, data_addr=0, data_read_n=3, fetch_restart=0, fetch_stall=0):
        # Wait until phase 7 to apply new request state so it's picked up on phase 0
        while self.phase != 7:
            await RisingEdge(self.clk)
        
        self.req_instr_addr = instr_addr
        self.req_data_addr = data_addr
        self.req_data_read_n = data_read_n
        self.req_instr_restart = fetch_restart
        self.req_instr_stall = fetch_stall
        
        # Wait for the request to be transmitted (8 phases)
        for _ in range(8):
            await RisingEdge(self.clk)

        # Revert to safe idle (no read)
        self.req_instr_restart = 0
        self.req_instr_stall = 0
        self.req_data_read_n = 3 # 3 = no read

async def monitor_qspi_tx(dut):
    """Monitors the SPI pins directly from uio_out to capture address sent to flash."""
    captured_nibbles = []
    
    # QSPI pins mapped in project.v:
    # qspi_flash_select is uio_out[0]
    # qspi_clk_out is uio_out[3]
    # qspi_data_out is {uio_out[5:4], uio_out[2:1]}
    # qspi_data_oe is {uio_oe[5:4], uio_oe[2:1]}
    
    # Wait for CS to go low
    while True:
        await RisingEdge(dut.clk)
        try:
            cs = dut.uio_out.value.to_unsigned() & 1
            if cs == 0:
                break
        except ValueError:
            continue
        
    prev_clk = (dut.uio_out.value.to_unsigned() >> 3) & 1
    
    while True:
        await RisingEdge(dut.clk)
        try:
            cs = dut.uio_out.value.to_unsigned() & 1
            if cs == 1:
                break
                
            curr_clk = (dut.uio_out.value.to_unsigned() >> 3) & 1
            if prev_clk == 0 and curr_clk == 1:
                uio_oe = dut.uio_oe.value.to_unsigned()
                data_oe = ((uio_oe >> 4) & 3) << 2 | ((uio_oe >> 1) & 3)
                if data_oe > 0:
                    uio_out = dut.uio_out.value.to_unsigned()
                    nibble = ((uio_out >> 4) & 3) << 2 | ((uio_out >> 1) & 3)
                    captured_nibbles.append(nibble)
                else:
                    break
            prev_clk = curr_clk
        except ValueError:
            pass
            
    return captured_nibbles

@cocotb.test()
async def test_blackbox(dut):
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    dut.rst_n.value = 0
    dut.ena.value = 0
    dut.ui_in.value = 0x70
    dut.uio_in.value = 0
    
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    dut.ena.value = 1
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    
    bridge = FpgaMemBridge(dut)
    
    test_data_addr = 0xABCDEF
    
    # Start monitor
    monitor_task = cocotb.start_soon(monitor_qspi_tx(dut))
    
    # Send request
    await bridge.request(instr_addr=0, data_addr=test_data_addr, data_read_n=0, fetch_restart=0, fetch_stall=1)
    
    # Wait for capture
    captured_nibbles = await monitor_task
    
    expected_address = [0xA, 0xB, 0xC, 0xD, 0xE, 0xF]
    
    assert len(captured_nibbles) >= 6, f"Did not capture enough nibbles. Got: {captured_nibbles}"
    assert captured_nibbles[0:6] == expected_address, f"Address mismatch. Expected {expected_address}, got {captured_nibbles[0:6]}"
    
    if len(captured_nibbles) >= 8:
        assert captured_nibbles[6:8] == [0xA, 0xA], "Mode bits did not match expected 4'b1010"
        
    dut._log.info(f"SUCCESS: Blackbox captured exact QSPI TX sequence: {[hex(n) for n in captured_nibbles]}")
