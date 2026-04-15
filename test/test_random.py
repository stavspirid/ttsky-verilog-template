import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, ReadOnly, Timer
from cocotb.handle import Force
import random

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
        await Timer(2, unit='ns')
        
        cs = get_cs(dut)
        sck = get_sck(dut)
        nibble_out = get_qspi_nibble_out(dut)
        
        await Timer(1, "ps")
        
        if cs == 1:
            state = "IDLE"
            set_qspi_nibble_in(dut, 0xF)
            prev_sck = sck
            continue
            
        if prev_sck == 0 and sck == 1: # SCK Rising edge
            if state == "IDLE":
                state = "ADDR"
                nibble_count = 0
                addr = 0
            if state == "ADDR":
                addr = (addr << 4) | nibble_out
                nibble_count += 1
                if nibble_count == 6:
                    state = "DUMMY"
                    nibble_count = 0
            elif state == "DUMMY":
                nibble_count += 1
                if nibble_count == 6: # 2 mode + 4 dummy
                    state = "DATA"
                    nibble_count = 0
            elif state == "DATA":
                pass # Host samples data here
                
        elif prev_sck == 1 and sck == 0: # SCK Falling edge
            if state == "DATA":
                if nibble_count % 2 == 0:
                    byte_val = memory.get(addr, 0x00)
                    set_qspi_nibble_in(dut, byte_val >> 4)
                else:
                    byte_val = memory.get(addr, 0x00)
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
                await Timer(2, unit='ns')
                try: frame_bytes.append(int(self.dut.uo_out.value))
                except ValueError: frame_bytes.append(0)
                
            status = frame_bytes[7]
            fetch_started = (status >> 7) & 1
            fetch_stopped = (status >> 6) & 1
            instr_ready = (status >> 5) & 1
            data_ready = (status >> 4) & 1
            
            instr_data = frame_bytes[0] | (frame_bytes[1] << 8)
            data_read = frame_bytes[2] | (frame_bytes[3] << 8) | (frame_bytes[4] << 16) | (frame_bytes[5] << 24)
            
            if fetch_started or fetch_stopped or instr_ready or data_ready:
                self.responses.append({
                    'fetch_started': fetch_started,
                    'fetch_stopped': fetch_stopped,
                    'instr_ready': instr_ready,
                    'data_ready': data_ready,
                    'instr_data': instr_data,
                    'data_read': data_read
                })

@cocotb.test()
async def test_randomized_combinations(dut):
    """
    Constrained-random test to stress test combinations of:
    - Data read widths (8-bit, 16-bit, 32-bit)
    - Instruction fetch states (branching, stalling, continuous running)
    - Overlapping/interleaving of data reads and instruction fetches
    - Random delays between host requests
    """
    await setup_dut(dut)
    
    # 1. Pre-populate mock memory with a large amount of random data
    memory = {}
    for _ in range(2000):
        addr = random.randint(0, 0x3FFFFF) # Random 22-bit address
        memory[addr] = random.randint(0, 255)
        
    cocotb.start_soon(mock_flash(dut, memory))
    monitor = ResponseMonitor(dut)
    
    NUM_ITERATIONS = 100 # Adjust as needed
    
    dut._log.info(f"Starting {NUM_ITERATIONS} randomized iterations...")
    
    for i in range(NUM_ITERATIONS):
        # 2. Constrained Randomization of Inputs
        # 10% chance to force a branch/restart
        do_fetch_restart = random.choices([0, 1], weights=[90, 10])[0] 
        # 25% chance to stall the instruction fetch
        do_fetch_stall = random.choices([0, 1], weights=[75, 25])[0]   
        
        # Random aligned instruction address
        instr_addr = random.randint(0, 0x0FFFFF) & ~1 
        # Random data address
        data_addr = random.randint(0, 0x3FFFFF)
        
        # 0: 8-bit, 1: 16-bit, 2: 32-bit (3 is assumed invalid/unused based on design)
        data_read_width = random.choice([0, 1, 2])
        
        # 3. Build and send the randomized frame
        frame = build_frame(
            fetch_restart=do_fetch_restart,
            fetch_stall=do_fetch_stall,
            instr_addr=instr_addr,
            data_addr=data_addr,
            data_read_n=data_read_width
        )
        
        if i % 10 == 0:
             dut._log.info(f"Iter {i}/{NUM_ITERATIONS}: restart={do_fetch_restart}, stall={do_fetch_stall}, i_addr={hex(instr_addr)}, d_addr={hex(data_addr)}, width={data_read_width}")
                      
        await send_frame(dut, frame)
        
        # 4. Random timing variation
        idle_cycles = random.randint(0, 5)
        for _ in range(idle_cycles):
            await send_frame(dut, build_frame(fetch_stall=do_fetch_stall))
            
    # 5. Flush out remaining operations
    dut._log.info("Flushing remaining operations...")
    for _ in range(50):
        await send_frame(dut, build_frame(fetch_stall=1))
        
    monitor.running = False
    dut._log.info(f"PASS: Completed {NUM_ITERATIONS} randomized test iterations.")
