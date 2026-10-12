# Dobot Magician Python API Reference

This document describes the Python API for connecting to and controlling the **Dobot Magician**
robotic arm. The API is provided by `lib/DobotDllType.py`, which wraps the native Dobot DLL
(`DobotDll.dll` on Windows, `libDobotDll.dylib` on macOS, `libDobotDll.so` on Linux) using Python's
`ctypes` module.

All functions in this document live in the module `lib/DobotDllType` (commonly imported as `dType`):

```python
from lib import DobotDllType as dType
```

---

## Table of Contents

1. [Requirements](#requirements)
2. [Loading the Library](#loading-the-library)
3. [Connection APIs](#connection-apis)
4. [Device Information APIs](#device-information-apis)
5. [Command Queue Control](#command-queue-control)
6. [Common Enums](#common-enums)
7. [Motion APIs (Quick Reference)](#motion-apis-quick-reference)
8. [End Effector APIs](#end-effector-apis)
9. [I/O APIs](#io-apis)
10. [Complete Connection Example](#complete-connection-example)

---

## Requirements

| Item | Requirement |
| --- | --- |
| Python | **64-bit** Python interpreter (the shipped DLL is 64-bit) |
| DLL | Must be present in `./lib/` (Windows) or on the library path |
| Architecture | 32-bit OS → 32-bit DLL, 64-bit OS → 64-bit DLL |
| Port | Correct serial COM port (e.g. `COM40`) and baud rate (default `115200`) |

> **Warning:** The `load()` function prints a reminder that the DLL is 64-bit and your Python
> environment must also be 64-bit.

---

## Loading the Library

### `load()`

Loads the native Dobot dynamic library into memory and returns a `CDLL` instance (`api`).
This `api` object is passed as the **first argument** to every other API function.

```python
def load():
    if platform.system() == "Windows":
        return CDLL("./lib/DobotDll.dll", RTLD_GLOBAL)
    elif platform.system() == "Darwin":
        return CDLL("./lib/DobotDll.dylib", RTLD_GLOBAL)
    elif platform.system() == "Linux":
        return cdll.loadLibrary("libDobotDll.so")
```

**Returns:** `CDLL` — the API handle used by all subsequent calls.

```python
api = dType.load()   # Load the DLL and get the CDLL object
```

---

## Connection APIs

These are the core functions required to establish and tear down a connection to the robot.

### `SearchDobot(api, maxLen=1000)`

Scans all serial ports and returns a list of the Dobot devices found.

| Parameter | Type | Description |
| --- | --- | --- |
| `api` | `CDLL` | Handle returned by `load()` |
| `maxLen` | `int` | Maximum length of the result buffer (default `1000`) |

**Returns:** `list[str]` — each element is a device string of the form `"PortName (DeviceName)"`.
Returns an empty list `[]` if no device is found.

```python
devices = dType.SearchDobot(api)
print("Found devices:", devices)   # e.g. ['COM40 (Dobot Magician)']
```

---

### `ConnectDobot(api, portName, baudrate)`

Opens a serial connection to the Dobot on the given port and baud rate.

| Parameter | Type | Description |
| --- | --- | --- |
| `api` | `CDLL` | Handle returned by `load()` |
| `portName` | `str` | Serial port name, e.g. `"COM40"` (Windows) or `"/dev/ttyUSB0"` (Linux/macOS) |
| `baudrate` | `int` | Communication speed, typically `115200` |

**Returns:** `list` of 8 elements:

| Index | Name | Description |
| --- | --- | --- |
| `0` | `result` | Connection status code — see [`DobotConnect`](#dobotconnect) |
| `1` | `masterDevType` | Master device type — see [`DevType`](#devtype) |
| `2` | `slaveDevType` | Slave device type (linear rail / Magician Lite), or `0` |
| `3` | `fwName` | Firmware name (string) |
| `4` | `fwVer` | Firmware version (string) |
| `5` | `masterId` | Internal master device ID |
| `6` | `slaveId` | Internal slave device ID (`-1`/`0` when unused) |
| `7` | `runTime` | Device run time (float) |

```python
state = dType.ConnectDobot(api, "COM40", 115200)[0]

if state == dType.DobotConnect.DobotConnect_NoError:
    print("Connected successfully")
```

> **Note:** The connection is only considered successful when
> `result == DobotConnect.DobotConnect_NoError (0)`.

---

### `DisconnectDobot(api)`

Closes the connection to the robot.

| Parameter | Type | Description |
| --- | --- | --- |
| `api` | `CDLL` | Handle returned by `load()` |

```python
dType.DisconnectDobot(api)
```

---

### `SetCmdTimeout(api, times)`

Sets the communication command timeout.

| Parameter | Type | Description |
| --- | --- | --- |
| `api` | `CDLL` | Handle returned by `load()` |
| `times` | `int` | Timeout value |

```python
dType.SetCmdTimeout(api, 100)
```

---

### `GetMarlinVersion(api)`

Requests the Marlin (firmware) version from the device.

```python
dType.GetMarlinVersion(api)
```

---

### `DobotExec(api)`

Executes pending commands (used internally by the DLL).

**Returns:** `list` containing the raw result code.

```python
result = dType.DobotExec(api)
```

---

## Device Information APIs

These functions are useful right after connecting to identify the device.

### `GetDeviceVersion(api)`

Returns the firmware/hardware version of the connected device.

**Returns:** `list[int]` — for Magician/Magician Lite:
`[fw_major, fw_minor, fw_revision, fw_alpha, hw_major, hw_minor, hw_revision, hw_alpha]`.
For a controller (rail) device, only the firmware fields are returned.

```python
version = dType.GetDeviceVersion(api)
print("Firmware:", version[0], version[1], version[2])
```

---

### `GetDeviceSN(api)`

**Returns:** `list[str]` — the device serial number.

```python
sn = dType.GetDeviceSN(api)[0]
```

---

### `SetDeviceSN(api, str)`

Sets the device serial number.

| Parameter | Type | Description |
| --- | --- | --- |
| `str` | `str` | New serial number |

```python
dType.SetDeviceSN(api, "MY-DOBOT-001")
```

---

### `GetDeviceName(api)`

**Returns:** `list[str]` — the device name.

```python
name = dType.GetDeviceName(api)[0]
```

---

### `SetDeviceName(api, str)`

Sets the device name.

```python
dType.SetDeviceName(api, "Magician-A")
```

---

### `GetDeviceTime(api)`

**Returns:** `list[int]` — the device's internal time value.

```python
t = dType.GetDeviceTime(api)[0]
```

---

### `GetDeviceID(api)`

**Returns:** `list` — `[result, deviceID1, deviceID2, deviceID3]`.
Returns `[result, 0, 0, 0]` if the read times out.

```python
device_id = dType.GetDeviceID(api)
```

---

### `GetDeviceInfo(api)`

**Returns:** `list` — `[deviceRunTime, devicePowerOn, devicePowerOff]`.

```python
info = dType.GetDeviceInfo(api)
print("Run time:", info[0], "Power-on count:", info[1])
```

---

### `GetDeviceWithL(api)` / `SetDeviceWithL(api, isWithL, version=0, isQueued=0)`

Reads or sets whether the device is used together with a **linear rail**.

```python
is_with_l = dType.GetDeviceWithL(api)[0]
```

---

### `GetAlarmsState(api, maxLen=1000)`

**Returns:** `list` — `[alarmsStateRawBytes, length]`.

```python
alarms = dType.GetAlarmsState(api)
```

---

### `ClearAllAlarmsState(api)`

Clears all active alarm states.

```python
dType.ClearAllAlarmsState(api)
```

---

### `GetPose(api)`

Reads the current pose of the arm.

**Returns:** `list[float]` —
`[x, y, z, rHead, joint1Angle, joint2Angle, joint3Angle, joint4Angle]`.

```python
pose = dType.GetPose(api)
x, y, z, r = pose[0], pose[1], pose[2], pose[3]
```

---

## Command Queue Control

The Dobot Magician executes commands from a queue. Setting `isQueued=1` on a command appends it to
the queue; setting `isQueued=0` executes it immediately.

### `SetQueuedCmdClear(api)`

Clears all commands from the queue.

```python
dType.SetQueuedCmdClear(api)
```

---

### `SetQueuedCmdStartExec(api)`

Starts executing the command queue.

```python
dType.SetQueuedCmdStartExec(api)
```

---

### `SetQueuedCmdStopExec(api)`

Stops executing the command queue (finishes the current command).

```python
dType.SetQueuedCmdStopExec(api)
```

---

### `SetQueuedCmdForceStopExec(api)`

Forcefully stops the command queue immediately.

```python
dType.SetQueuedCmdForceStopExec(api)
```

---

### `GetQueuedCmdCurrentIndex(api)`

**Returns:** `list[int]` — `[currentIndex, currentIndex1]`. The running index is used to wait for a
queued command to finish by comparing it against the index returned when the command was added.

```python
last_index = dType.SetPTPCmd(api, dType.PTPMode.PTPMOVLXYZMode, 200, 0, 0, 0, isQueued=1)[0]
while last_index > dType.GetQueuedCmdCurrentIndex(api)[0]:
    dType.dSleep(20)
```

---

### `GetQueuedCmdMotionFinish(api)`

**Returns:** `list[bool]` — `True` when the queued motion has finished.

```python
finished = dType.GetQueuedCmdMotionFinish(api)[0]
```

---

### `SetQueuedCmdStartDownload(api, totalLoop, linePerLoop)` / `SetQueuedCmdStopDownload(api)`

Used for streaming command files to the device.

```python
dType.SetQueuedCmdStartDownload(api, 1, 100)
# ... send commands ...
dType.SetQueuedCmdStopDownload(api)
```

---

### `dSleep(ms)`

Suspends execution for a number of milliseconds (Python helper, not a DLL call).

```python
dType.dSleep(100)   # sleep 100 ms
```

---

## Common Enums

### `DobotConnect`

Connection result codes returned by `ConnectDobot`.

| Name | Value | Meaning |
| --- | --- | --- |
| `DobotConnect_NoError` | `0` | Connected successfully |
| `DobotConnect_NotFound` | `1` | Device not found |
| `DobotConnect_Occupied` | `2` | Port is already occupied |

```python
CON_STR = {
    dType.DobotConnect.DobotConnect_NoError:  "DobotConnect_NoError",
    dType.DobotConnect.DobotConnect_NotFound: "DobotConnect_NotFound",
    dType.DobotConnect.DobotConnect_Occupied: "DobotConnect_Occupied",
}
```

---

### `DobotCommunicate`

Communication result codes.

| Name | Value |
| --- | --- |
| `DobotCommunicate_NoError` | `0` |
| `DobotCommunicate_BufferFull` | `1` |
| `DobotCommunicate_Timeout` | `2` |
| `DobotCommunicate_InvalidParams` | `3` |
| `DobotCommunicate_InvalidDevice` | `4` |

---

### `DevType`

Device type codes returned by `ConnectDobot`.

| Name | Value |
| --- | --- |
| `Idle` | `0` |
| `Conntroller` | `1` |
| `Magician` | `2` |
| `MagicianLite` | `3` |

---

### `PTPMode`

Motion mode used by `SetPTPCmd`.

| Name | Value | Description |
| --- | --- | --- |
| `PTPJUMPXYZMode` | `0` | Jump (gate) motion to a Cartesian point |
| `PTPMOVJXYZMode` | `1` | Joint-interpolated move to a Cartesian point |
| `PTPMOVLXYZMode` | `2` | Linear move to a Cartesian point |
| `PTPJUMPANGLEMode` | `3` | Jump motion to a joint-angle pose |
| `PTPMOVJANGLEMode` | `4` | Joint-interpolated move to a joint-angle pose |
| `PTPMOVLANGLEMode` | `5` | Linear move to a joint-angle pose |

---

## Motion APIs (Quick Reference)

| Function | Purpose |
| --- | --- |
| `SetHOMEParams(api, x, y, z, r, isQueued=0)` | Set the HOME position |
| `GetHOMEParams(api)` | Read the HOME position |
| `SetHOMECmd(api, temp, isQueued=0)` | Execute homing |
| `SetPTPJointParams(api, ...)` | Set joint velocity/acceleration (8 values) |
| `SetPTPCoordinateParams(api, xyzVel, xyzAcc, rVel, rAcc, isQueued=0)` | Set Cartesian speed params |
| `SetPTPJumpParams(api, jumpHeight, zLimit, isQueued=0)` | Set jump (gate) motion params |
| `SetPTPCommonParams(api, velocityRatio, accelerationRatio, isQueued=0)` | Set global speed ratio |
| `SetPTPCmd(api, ptpMode, x, y, z, rHead, isQueued=0)` | Send a PTP motion command |
| `SetPTPWithLCmd(api, ptpMode, x, y, z, rHead, l, isQueued=0)` | PTP move for linear-rail setups |
| `SetCPParams(...)`, `SetCPCmd(...)` | Continuous-path (CP) motion |
| `SetARCCmd(api, cirPoint, toPoint, isQueued=0)` | Arc motion |
| `SetCircleCmd(api, cirPoint, toPoint, isQueued=0)` | Full circle motion |
| `SetWAITCmd(api, waitTime, isQueued=0)` | Insert a wait into the queue |
| `SetJointSpeedRatio` / `SetArmSpeedRatio(...)` | Adjust arm speed ratio |

```python
# Typical queued linear move
dType.SetPTPCmd(api, dType.PTPMode.PTPMOVLXYZMode, 200, 0, 0, 0, isQueued=1)
```

---

## End Effector APIs

| Function | Purpose |
| --- | --- |
| `SetEndEffectorParams(api, xBias, yBias, zBias, isQueued=0)` | Set tool offset |
| `GetEndEffectorParams(api)` | Read tool offset |
| `SetEndEffectorLaser(api, enableCtrl, on, isQueued=0)` | Control the laser |
| `SetEndEffectorSuctionCup(api, enableCtrl, on, isQueued=0)` | Control the suction cup |
| `SetEndEffectorGripper(api, enableCtrl, on, isQueued=0)` | Control the gripper |
| `SetEndEffectorType(api, endType=0, isQueued=0)` | Set end effector type (pen/gripper/etc.) |
| `SetServoAngle(api, servoId, angle, isQueued=0)` | Set servo (gripper) angle |

```python
# Turn the suction cup on
dType.SetEndEffectorSuctionCup(api, 1, 1, isQueued=1)
```

---

## I/O APIs

| Function | Purpose |
| --- | --- |
| `SetIOMultiplexing(api, address, multiplex, isQueued=0)` | Configure pin function |
| `SetIODO(api, address, level, isQueued=0)` | Set digital output level |
| `GetIODO(api, addr)` | Read digital output |
| `GetIODI(api, addr)` | Read digital input |
| `SetIOPWM(api, address, frequency, dutyCycle, isQueued=0)` | Set PWM output |
| `GetIOPWM(api, addr)` | Read PWM output |
| `GetIOADC(api, addr)` | Read ADC value |
| `SetEMotor(api, index, isEnabled, speed, isQueued=0)` | Control a stepper motor |
| `SetEMotorS(api, index, isEnabled, speed, distance, isQueued=0)` | Control a stepper motor with distance |

> The `...Ext` / `...ExtEx` variants (e.g. `SetIODOExt`, `SetIOMultiplexingExtEx`) target the
> controller/Magic Box (`slaveId = -1`) and provide synchronous "wait until executed" behavior.

---

## Complete Connection Example

Minimal script that connects, homes, performs a move, then disconnects:

```python
from lib import DobotDllType as dType

CON_STR = {
    dType.DobotConnect.DobotConnect_NoError:  "DobotConnect_NoError",
    dType.DobotConnect.DobotConnect_NotFound: "DobotConnect_NotFound",
    dType.DobotConnect.DobotConnect_Occupied: "DobotConnect_Occupied",
}

# 1. Load the DLL and get the CDLL object
api = dType.load()

# 2. Optionally scan for available devices
print("Available devices:", dType.SearchDobot(api))

# 3. Connect to the robot
state = dType.ConnectDobot(api, "COM40", 115200)[0]
print("Connect status:", CON_STR[state])

if state == dType.DobotConnect.DobotConnect_NoError:
    # Reset the command queue
    dType.SetQueuedCmdClear(api)

    # Configure motion parameters
    dType.SetHOMEParams(api, 200, 200, 200, 200, isQueued=1)
    dType.SetPTPJointParams(api, 200, 200, 200, 200, 200, 200, 200, 200, isQueued=1)
    dType.SetPTPCommonParams(api, 100, 100, isQueued=1)

    # Home the robot
    dType.SetHOMECmd(api, temp=0, isQueued=1)

    # Queue a linear move and remember its index
    last_index = dType.SetPTPCmd(
        api, dType.PTPMode.PTPMOVLXYZMode, 200, 50, 50, 50, isQueued=1
    )[0]

    # Start executing queued commands
    dType.SetQueuedCmdStartExec(api)

    # Wait for the last command to finish
    while last_index > dType.GetQueuedCmdCurrentIndex(api)[0]:
        dType.dSleep(100)

    # Stop the queue
    dType.SetQueuedCmdStopExec(api)

# 4. Disconnect
dType.DisconnectDobot(api)
```

---

## Reference: Real-World Wrapper

`testControl.py` shows a practical wrapper class (`DobotController`) built on top of these APIs.
Its connection method illustrates the recommended startup sequence:

```python
def connect(self):
    state = dType.ConnectDobot(self.api, self.port, self.baudrate)[0]
    if state == dType.DobotConnect.DobotConnect_NoError:
        self.is_connected = True
        dType.SetQueuedCmdClear(self.api)
        dType.SetHOMEParams(self.api, 200, 200, 200, 200, isQueued=1)
        dType.SetPTPJointParams(self.api, 200, 200, 200, 200, 200, 200, 200, 200, isQueued=1)
        dType.SetPTPCommonParams(self.api, 100, 100, isQueued=1)
        dType.SetQueuedCmdStartExec(self.api)
        return True
    return False
```

The safe shutdown sequence used before disconnecting:

```python
def disconnect(self):
    if self.is_connected:
        dType.SetQueuedCmdStopExec(self.api)
        dType.DisconnectDobot(self.api)
```
