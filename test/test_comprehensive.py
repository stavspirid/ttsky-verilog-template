import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, ReadOnly, Timer
from cocotb.handle import Force
from cocotb.types import LogicArray

CS_BIT  = 0
SD0_BIT = 1
SD1_BIT = 2
SCK_BIT = 3
SD2_BIT = 4
SD3_BIT = 5

SAFE_IDLE = 0x70

def get_cs(dut):
    try: return (int(dut.user_project.uio_out.value) >> CS_BIT) & 1
    except ValueError: return 1

def get_sck(dut):
    try: return (int(dut.user_project.uio_out.value) >> SCK_BIT) & 1
    except ValueError: return 0

def get_qspi_nibble_out(dut):
    try: u = int(dut.user_project.uio_out.value)
    except ValueError: return 0
    return (((u >> SD3_BIT) & 1) << 3 |
            ((u >> SD2_BIT) & 1) << 2 |
            ((u >> SD1_BIT) & 1) << 1 |
            ((u >> SD0_BIT) & 1))

def set_qspi_nibble_in(dut, nibble):
    if hasattr(dut, 'user_project'):
        try: u = int(dut.user_project.uio_in.value)
        except ValueError: u = 0xFF
        u &= ~( (1<<5) | (1<<4) | (1<<2) | (1<<1) )
        u |= ((nibble >> 2) & 3) << 4
        u |= ((nibble >> 0) & 3) << 1
        dut.user_project.uio_in.value = Force(u)

async def mock_flash(dut, memory):
    prev_sck = 1
    state = "IDLE"
    addr = 0
    nibble_count = 0
    
    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()
        
        cs = get_cs(dut)
        sck = get_sck(dut)
        nibble_out = get_qspi_nibble_out(dut)
        
        await Timer(1, "ps")
        
        if cs == 1:
            if state != "IDLE":
                dut._log.info("mock_flash: CS high, returning to IDLE")
            state = "IDLE"
            set_qspi_nibble_in(dut, 0xF)
            prev_sck = sck
            continue
            
        if prev_sck == 0 and sck == 1: # SCK Rising edge
            if state == "IDLE":
                state = "ADDR"
                nibble_count = 0
                addr = 0
                dut._log.info("mock_flash: CS low, starting ADDR phase")
                
            if state == "ADDR":
                addr = (addr << 4) | nibble_out
                nibble_count += 1
                if nibble_count == 6:
                    dut._log.info(f"mock_flash: ADDR phase complete. Addr={hex(addr)}")
                    state = "DUMMY"
                    nibble_count = 0
            elif state == "DUMMY":
                nibble_count += 1
                if nibble_count == 6: # 2 mode + 4 dummy
                    dut._log.info("mock_flash: DUMMY phase complete, starting DATA phase")
                    state = "DATA"
                    nibble_count = 0
            elif state == "DATA":
                pass # Host samples data here
                
        elif prev_sck == 1 and sck == 0: # SCK Falling edge
            if state == "DATA":
                if nibble_count % 2 == 0:
                    byte_val = memory.get(addr, 0x00)
                    dut._log.info(f"mock_flash: Outputting high nibble of {hex(byte_val)} at addr {hex(addr)}")
                    set_qspi_nibble_in(dut, byte_val >> 4)
                else:
                    byte_val = memory.get(addr, 0x00)
                    dut._log.info(f"mock_flash: Outputting low nibble of {hex(byte_val)} at addr {hex(addr)}")
                    set_qspi_nibble_in(dut, byte_val & 0xF)
                    addr += 1
                nibble_count += 1

        prev_sck = sck

def build_frame(fetch_restart=0, fetch_stall=0, instr_addr=0, data_addr=0, data_read_n=3):
    return [
        (fetch_restart << 7) | (fetch_stall << 6) | ((instr_addr >> 18) & 0x3F),
        (instr_addr >> 10) & 0xFF,
        (instr_addr >>  2) & 0xFF,
        (((instr_addr >> 1) & 1) << 7) | (((data_addr >> 18) & 0x3F) << 1),
        (data_addr >> 10) & 0xFF,
        (data_addr >>  2) & 0xFF,
        ((data_addr & 0x03) << 6) | ((data_read_n & 0x03) << 4),
        SAFE_IDLE,
    ]

async def send_frame(dut, frame):
    for byte in frame:
        dut.ui_in.value = byte
        await RisingEdge(dut.clk)
    dut.ui_in.value = SAFE_IDLE

async def setup_dut(dut):
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    dut.rst_n.value  = 0
    dut.ena.value    = 1
    dut.ui_in.value  = SAFE_IDLE
    dut.uio_in.value = 0
    await ClockCycles(dut.clk, 8)
    dut.rst_n.value = 1
    await send_frame(dut, build_frame(fetch_restart=0, fetch_stall=1))

class ResponseMonitor:
    def __init__(self, dut):
        self.dut = dut
        self.responses = []
        self.running = True
        cocotb.start_soon(self._run())
        
    async def _run(self):
        while self.running:
            frame_bytes = []
            for _ in range(8):
                await RisingEdge(self.dut.clk)
                await ReadOnly()
                try: frame_bytes.append(int(self.dut.uo_out.value))
                except ValueError: frame_bytes.append(0)
                
            status = frame_bytes[7]
            fetch_started = (status >> 7) & 1
            fetch_stopped = (status >> 6) & 1
            instr_ready = (status >> 5) & 1
            data_ready = (status >> 4) & 1
            
            instr_data = frame_bytes[0] | (frame_bytes[1] << 8)
            data_read = frame_bytes[2] | (frame_bytes[3] << 8) | (frame_bytes[4] << 16) | (frame_bytes[5] << 24)
            
            # self.dut._log.info(f"ResponseMonitor: frame_bytes={[hex(b) for b in frame_bytes]}")
            
            if fetch_started or fetch_stopped or instr_ready or data_ready:
                self.dut._log.info(f"ResponseMonitor: Got status byte {hex(status)}. frame={[hex(b) for b in frame_bytes]}")
                self.responses.append({
                    'fetch_started': fetch_started,
                    'fetch_stopped': fetch_stopped,
                    'instr_ready': instr_ready,
                    'data_ready': data_ready,
                    'instr_data': instr_data,
                    'data_read': data_read
                })

@cocotb.test()
async def test_comprehensive_data_reads(dut):
    """
    Test 8-bit, 16-bit, and 32-bit data reads.
    """
    await setup_dut(dut)
    
    memory = {
        0x100000: 0x11, 0x100001: 0x22, 0x100002: 0x33, 0x100003: 0x44,
        0x200000: 0xAA, 0x200001: 0xBB,
        0x300000: 0xCC
    }
    
    cocotb.start_soon(mock_flash(dut, memory))
    monitor = ResponseMonitor(dut)
    
    # 1. Test 8-bit read
    dut._log.info("Sending 8-bit data read...")
    await send_frame(dut, build_frame(data_addr=0x300000, data_read_n=0, fetch_stall=1))
    
    # Wait for response
    for _ in range(10):
        await send_frame(dut, build_frame(fetch_stall=1))
        if monitor.responses:
            break
            
    assert len(monitor.responses) > 0, "No response for 8-bit read"
    resp = monitor.responses.pop(0)
    assert resp['data_ready'] == 1, "Data not ready"
    # The read length is 1 byte, so it should be mapped to the lowest byte of data_from_read
    assert (resp['data_read'] & 0xFF) == 0xCC, f"Expected 0xCC, got {hex(resp['data_read'])}"
    
    # 2. Test 16-bit read
    dut._log.info("Sending 16-bit data read...")
    await send_frame(dut, build_frame(data_addr=0x200000, data_read_n=1, fetch_stall=1))
    
    for _ in range(10):
        await send_frame(dut, build_frame(fetch_stall=1))
        if monitor.responses:
            break
            
    assert len(monitor.responses) > 0, "No response for 16-bit read"
    resp = monitor.responses.pop(0)
    assert resp['data_ready'] == 1, "Data not ready"
    # Data is expected in Little Endian: 0xBB AA
    assert (resp['data_read'] & 0xFFFF) == 0xBBAA, f"Expected 0xBBAA, got {hex(resp['data_read'])}"
    
    # 3. Test 32-bit read
    dut._log.info("Sending 32-bit data read...")
    await send_frame(dut, build_frame(data_addr=0x100000, data_read_n=2, fetch_stall=1))
    
    for _ in range(10):
        await send_frame(dut, build_frame(fetch_stall=1))
        if monitor.responses:
            break
            
    assert len(monitor.responses) > 0, "No response for 32-bit read"
    resp = monitor.responses.pop(0)
    assert resp['data_ready'] == 1, "Data not ready"
    assert resp['data_read'] == 0x44332211, f"Expected 0x44332211, got {hex(resp['data_read'])}"
    
    monitor.running = False
    dut._log.info("PASS: All data reads successful")

@cocotb.test()
async def test_comprehensive_instr_fetch(dut):
    """
    Test continuous instruction fetch, stall, and resume.
    """
    await setup_dut(dut)
    
    memory = {
        0x000000: 0x13, 0x000001: 0x01, # NOP
        0x000002: 0x37, 0x000003: 0x02, # LUI
        0x000004: 0x13, 0x000005: 0x00, # ADDI
    }
    
    cocotb.start_soon(mock_flash(dut, memory))
    monitor = ResponseMonitor(dut)
    
    # 1. Start fetch
    dut._log.info("Starting instruction fetch...")
    await send_frame(dut, build_frame(fetch_restart=1, fetch_stall=0, instr_addr=0x000000))
    
    # Send idle frames allowing fetch to continue
    for _ in range(15):
        await send_frame(dut, build_frame(fetch_restart=0, fetch_stall=0))
        
    # We should have received some instr_ready
    ready_count = 0
    for resp in monitor.responses:
        if resp['fetch_started']:
            dut._log.info("Fetch started")
        if resp['instr_ready']:
            ready_count += 1
            dut._log.info(f"Instr received: {hex(resp['instr_data'])}")
            
    assert ready_count > 0, "No instructions received"
    
    # 2. Stall fetch
    dut._log.info("Stalling instruction fetch...")
    monitor.responses.clear()
    
    for _ in range(5):
        await send_frame(dut, build_frame(fetch_restart=0, fetch_stall=1))
        
    for resp in monitor.responses:
        if resp['fetch_stopped']:
            dut._log.info("Fetch stopped due to stall")
    
    # 3. Resume fetch from middle
    dut._log.info("Resuming fetch from 0x000004...")
    monitor.responses.clear()
    await send_frame(dut, build_frame(fetch_restart=1, fetch_stall=0, instr_addr=0x000004))
    
    for _ in range(15):
        await send_frame(dut, build_frame(fetch_restart=0, fetch_stall=0))
        
    resumed_ok = False
    for resp in monitor.responses:
        if resp['instr_ready'] and resp['instr_data'] == 0x0013:
            resumed_ok = True
            dut._log.info("Resumed instruction correctly")
            break
            
    assert resumed_ok, "Did not receive the correct instruction after resume"
            
    monitor.running = False
    dut._log.info("PASS: Instruction fetch control successful")
