from fastapi import FastAPI, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException
from typing import List
import time


# Initialize FastAPI app
app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup Modbus RTU client
modbus_client = ModbusSerialClient(
    port="/dev/ttyUSB1",   # Change this if using another serial port
    baudrate=9600,
    stopbits=1,
    bytesize=8,
    parity='E',
    timeout=1
)

is_connected = modbus_client.connect()

# Request model for a single command
class CommandRequest(BaseModel):
    action: str         # "coil", "temp", "fan_speed"
    value: int
    address: int
    slave_id: int = 1   # Optional default value

# Device control helpers
def write_coil(address: int, value: int, slave_id: int) -> bool:
    try:
        result = modbus_client.write_coil(address, value, slave=slave_id)
        return not result.isError()
    except ModbusException:
        return False

def write_register(address: int, value: int, slave_id: int) -> bool:
    try:
        result = modbus_client.write_register(address, value, slave=slave_id)
        return not result.isError()
    except ModbusException:
        return False

# Background task to process a list of commands
def process_bulk_commands(commands: List[CommandRequest]):
    for cmd in commands:
        if cmd.action == "coil":
            write_coil(cmd.address, cmd.value, cmd.slave_id)
        elif cmd.action == "temp":
            write_register(cmd.address, cmd.value, cmd.slave_id)
        elif cmd.action == "fan_speed":
            write_register(cmd.address, cmd.value, cmd.slave_id)

# POST endpoint for single control command
@app.post("/api/control")
def control_device(cmd: CommandRequest):
    if not is_connected:
        return {"status": "error", "message": "❌ Modbus device not connected"}

    if cmd.action == "coil":
        success = write_coil(cmd.address, cmd.value, cmd.slave_id)
        msg = f"Coil at address {cmd.address} set to {cmd.value}"

    elif cmd.action == "temp":
        success = write_register(cmd.address, cmd.value, cmd.slave_id)
        msg = f"Temperature at address {cmd.address} set to {cmd.value / 10:.1f}°C"

    elif cmd.action == "fan_speed":
        success = write_register(cmd.address, cmd.value, cmd.slave_id)
        msg = f"Fan speed at address {cmd.address} set to {cmd.value}"

    else:
        return {"status": "error", "message": "Invalid action specified"}

    return {"status": "success" if success else "error", "message": msg}

# POST endpoint for bulk control (runs in background)
@app.post("/api/control/bulk")
def bulk_control_device(commands: List[CommandRequest], background_tasks: BackgroundTasks):
    if not is_connected:
        return {"status": "error", "message": "❌ Modbus device not connected"}

    background_tasks.add_task(process_bulk_commands, commands)
    return {
        "status": "success",
        "message": f"✅ {len(commands)} commands are being processed in the background"
    }

# GET endpoint to fetch current device status
@app.get("/api/status")
def get_device_data(
    slave_id: int = 1,
    register_address_on_off: int = Query(0, alias="on"),
    register_address_temp: int = Query(1, alias="temp"),
    register_address_speed: int = Query(36, alias="speed")
):
    if not is_connected:
        return {"Status": 1, "Temp": 20, "Speed": 1, "outside_temp": 66}

    try:
        # Read ON/OFF status
        discrete = modbus_client.read_discrete_inputs(register_address_on_off, slave=slave_id)
        input_status = int(discrete.bits[0]) if discrete and not discrete.isError() else 0

        # Read temperature
        temp_result = modbus_client.read_input_registers(register_address_temp, count=1, slave=slave_id)
        temperature = temp_result.registers[0] / 10 if temp_result and not temp_result.isError() else 0

        # Read fan speed
        speed_result = modbus_client.read_input_registers(register_address_speed, count=1, slave=slave_id)
        speed = speed_result.registers[0] if speed_result and not speed_result.isError() else 0

        return {
            "Status": input_status,
            "Temp": temperature,
            "Speed": speed
        }

    except ModbusException:
        return {"Status": "error", "message": "Failed to read from Modbus device"}








bulk_results = {}

# Define request body structure
class BulkRequest(BaseModel):
    slave_id: List[int]
    on: List[int]
    temp: List[int]
    speed: List[int]

@app.post("/api/status/bulk")
def get_bulk_device_data(request: BulkRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(
        fetch_bulk_data, request.slave_id, request.on, request.temp, request.speed
    )
    return {"message": "Bulk fetch started", "slave_ids": request.slave_id}


def fetch_bulk_data(slave_id: List[int], on: List[int], temp: List[int], speed: List[int]):
    results = []

    for i in range(len(slave_id)):
        try:
            status = get_device_data(
                slave_id=slave_id[i],
                register_address_on_off=on[i],
                register_address_temp=temp[i],
                register_address_speed=speed[i]
            )

            # Calculate vent numbers from temp and speed addresses
            vent_from_temp = int((temp[i] - 1) / 156 + 1)
            vent_from_speed = int((speed[i] - 36) / 156 + 1)

            # Cross verify both formulas
            if vent_from_temp == vent_from_speed:
                vent_number = vent_from_temp
            else:
                vent_number = None  # mismatch, handle gracefully

            results.append({
                "slave_id": slave_id[i],
                "Status": status["Status"],
                "Temp": status["Temp"],
                "Speed": status["Speed"],
                "vent_number": vent_number
            })

        except Exception as e:
            results.append({
                "slave_id": slave_id[i],
                "Status": "error",
                "message": str(e),
                "vent_number": None
            })

        time.sleep(0.1)  # optional delay

    bulk_results["last_run"] = results


@app.get("/api/status/bulk/results")
def get_bulk_results():
    return bulk_results if bulk_results else {"message": "No results yet"}
