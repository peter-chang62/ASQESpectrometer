# Spectrometer Control API Documentation

## Before using, install the libraries from requirements.txt

```bash
pip install -r requirements.txt
```

## Device Connection & Initialization

```python
from libspec import ASQESpectrometer

spec = ASQESpectrometer()                    # Class Initialization
spec.configure_acquisition()                 # Initial Device Configuration
```

### Standard Parameters Device Configuration

| Parameter              | Value | Description                                 |
| ---------------------- | ----- | ------------------------------------------- |
| `num_of_scans`         | 1     | Number of spectral averages per measurement |
| `num_of_blank_scans`   | 0     | Background reference measurements           |
| `exposure_time`        | 1000  | Integration time (10 ms)                    |
| `scan_mode`            | 3     | Acquisition mode (hardware-specific code)   |
| `num_of_start_element` | 0     | First CCD pixel index (start of array)      |
| `num_of_end_element`   | 3647  | Last CCD pixel index (3648-element array)   |
| `reduction_mode`       | 0     | Data processing mode (hardware-specific)    |

```python
# Corresponding initialization code
{
    "num_of_scans": 1,
    "num_of_blank_scans": 0,
    "exposure_time": 1000,
    "scan_mode": 3,
    "num_of_start_element": 0,
    "num_of_end_element": 3647,
    "reduction_mode": 0
}
```

### Change Parameters Device Configuration

```python
spec.set_parameters(
    num_of_scans=None,
    num_of_blank_scans=None,
    exposure_time=None,
    scan_mode=None,
    num_of_start_element=None,
    num_of_end_element=None,
    reduction_mode=None
)                                                    # Set New Device Configuration
spec.configure_acquisition()                 # Reconfigure with new parameters
```

### Parameters Device Configuration

| Parameter              | Values   | Description                                 |
| ---------------------- | -------- | ------------------------------------------- |
| `num_of_scans`         | 1 - 137  | Number of spectral averages per measurement |
| `num_of_blank_scans`   | 0 - 137  | Background reference measurements           |
| `exposure_time`        | 1000 ... | Integration time (10ms units)               |
| `scan_mode`            | 0 - 3    | Acquisition mode                            |
| `num_of_start_element` | 0        | First CCD pixel index (start of array)      |
| `num_of_end_element`   | 3647     | Last CCD pixel index (3648-element array)   |
| `reduction_mode`       | 0 - 3    | Data processing mode                        |

### Scan Modes

| Mode | Behavior                             |
| ---- | ------------------------------------ |
| 0    | Continuous read with trigger         |
| 1    | Idle before trigger, auto-read after |
| 2    | Idle between all frames              |
| 3    | Frame averaging (requires blank=0)   |

### Reduction Modes

| Mode | Pixel Averaging |
| ---- | --------------- |
| 0    | None (raw)      |
| 1    | 2:1             |
| 2    | 4:1             |
| 3    | 8:1             |

### Get the spectrum data

```python
spectrum = spec.get_spectrum()               # Get the spectrum data from spectrometer
```

### Get the spectrum data with subtract background

```python
spectrum = spec.subtract_background()               # Get the spectrum data from spectrometer with subtract background
```

### Get the spectrum data with normalization

```python
wavelength, intensity = spec.normalize_spectrum()  # Get the spectrum data from spectrometer with subtract background
```

### Get the spectrum data with calibration

```python
wavelength, intensity = spec.get_calibrated_spectrum()  # Get the spectrum data from spectrometer with subtract background
```

### Example: Get spectrum with standard parameters with normalization

```python
from libspec import ASQESpectrometer

spec = ASQESpectrometer()
spec.configure_acquisition()
spectrum = spec.normalize_spectrum()
```

### Example: Get spectrum with updated parameter (exposure time)

```python
from libspectr import ASQESpectrometer

spec = ASQESpectrometer()
spec.set_parameters(exposure_time=2500)
spec.configure_acquisition()
wavelength, spectrum = spec.normalize_spectrum()
```

### Example: Get and plot spectrum data

```python
from libspec import ASQESpectrometer
import matplotlib.pyplot as plt
import numpy as np

spec = ASQESpectrometer()
spec.configure_acquisition()
wavelength, intensity = spec.normalize_spectrum()

plt.figure(figsize=(10, 5))
plt.plot(wavelength, intensity, color='blue')
plt.title('Spectrometer Output')
plt.xlabel('Wavelength (nm)')
plt.ylabel('Intensity (a.u)')
plt.grid(True)
plt.tight_layout()
plt.show()
```

### Example: Real-time plotting during measurements (Jupyter)

```python
from libspec import ASQESpectrometer
import numpy as np
import matplotlib.pyplot as plt
from IPython.display import display, clear_output

spec = ASQESpectrometer()
spec.configure_acquisition()

fig, ax = plt.subplots(figsize=(10, 5))
plt.rcParams["font.size"] = 20
ax.set_xlabel('Wavelength (nm)', fontsize=18)
ax.set_ylabel('Intensity (a.u)', fontsize=18)
ax.grid(True)
line, = ax.plot([], [], color='blue')
display(fig)

running = True
measurement_count = 0
do_measurements = 1000

def on_key(event):
    global running
    if event.key.lower() == 'q':
        running = False

fig.canvas.mpl_connect('key_press_event', on_key)

try:
    while running and measurement_count < do_measurements:
        wavelength, intensity = spec.normalize_spectrum()
        line.set_data(wavelength, intensity)
        ax.relim()
        ax.autoscale_view()
        clear_output(wait=True)
        display(fig)
        fig.canvas.flush_events()
        measurement_count += 1
        print(f"Measurement {measurement_count}/{do_measurements}")
except KeyboardInterrupt:
    pass
finally:
    plt.close()
    print(f"Completed {measurement_count} measurements")
```

## Examples: Real-time graph

### Read and display data from ASQESpectrometer

Run using:

```
python spectrum.py
```

```python
# spectrum.py
from libspec import ASQESpectrometer
import numpy as np
import matplotlib.pyplot as plt

def main():
    spec = ASQESpectrometer()
    spec.configure_acquisition()

    plt.ion()
    fig, ax = plt.subplots(figsize=(10, 5))
    plt.rcParams["font.size"] = 20
    ax.set_xlabel('Pixel Index', fontsize=18)
    ax.set_ylabel('Intensity', fontsize=18)
    ax.grid(True)
    line, = ax.plot([], [], color='blue')

    running = True
    measurement_count = 0
    max_measurements = 1000

    def on_key(event):
        nonlocal running
        if event.key.lower() == 'q':
            running = False

    fig.canvas.mpl_connect('key_press_event', on_key)

    try:
        while running and measurement_count < max_measurements:
            spectrum = spec.get_spectrum()
            intensity = np.ctypeslib.as_array(spectrum)
            pixels = np.arange(len(intensity))

            line.set_data(pixels, intensity)
            ax.relim()
            ax.autoscale_view()
            fig.canvas.draw()
            fig.canvas.flush_events()
            measurement_count += 1
            plt.title(f"Measurement {measurement_count}/{max_measurements}, press 'q' to stop")
    except KeyboardInterrupt:
        pass
    finally:
        plt.ioff()
        plt.close()

if __name__ == '__main__':
    main()
```

### Read and display normalized spectrum from ASQESpectrometer

Run using:

```
python spectrum_norm.py
```

```python
# spectrum_norm.py

from libspec import ASQESpectrometer
import numpy as np
import matplotlib.pyplot as plt

def main():
    spec = ASQESpectrometer()
    spec.configure_acquisition()

    plt.ion()
    fig, ax = plt.subplots(figsize=(10, 5))
    plt.rcParams["font.size"] = 20
    ax.set_xlabel('Wavelength (nm)', fontsize=18)
    ax.set_ylabel('Intensity (uW/cm^2/nm)', fontsize=18)
    ax.grid(True)
    line, = ax.plot([], [], color='blue')

    running = True
    measurement_count = 0
    max_measurements = 1000


    def on_key(event):
        nonlocal running
        if event.key.lower() == 'q':
            running = False

    fig.canvas.mpl_connect('key_press_event', on_key)

    try:
        while running and measurement_count < max_measurements:
            wavelength, intensity = spec.get_calibrated_spectrum()
            line.set_data(wavelength, intensity)
            ax.relim()
            ax.autoscale_view()
            fig.canvas.draw()
            fig.canvas.flush_events()
            measurement_count += 1
            plt.title(f"Measurement {measurement_count}/{max_measurements}, press 'q' to stop")
    except KeyboardInterrupt:
        pass
    finally:
        plt.ioff()
        plt.close()

if __name__ == '__main__':
    main()
```

### Read and display power (calibrated) spectrum from ASQESpectrometer

Run using:

```
python spectrum_calib.py
```

```python

# spectrum_calib.py
from libspec import ASQESpectrometer
import numpy as np
import matplotlib.pyplot as plt

def main():
    spec = ASQESpectrometer()
    spec.configure_acquisition()

    plt.ion()
    fig, ax = plt.subplots(figsize=(10, 5))
    plt.rcParams["font.size"] = 20
    ax.set_xlabel('Wavelength (nm)', fontsize=18)
    ax.set_ylabel('Intensity (a.u.)', fontsize=18)
    ax.grid(True)
    line, = ax.plot([], [], color='blue')

    running = True
    measurement_count = 0
    max_measurements = 1000


    def on_key(event):
        nonlocal running
        if event.key.lower() == 'q':
            running = False

    fig.canvas.mpl_connect('key_press_event', on_key)

    try:
        while running and measurement_count < max_measurements:
            wavelength, intensity = spec.normalize_spectrum()
            line.set_data(wavelength, intensity)
            ax.relim()
            ax.autoscale_view()
            fig.canvas.draw()
            fig.canvas.flush_events()
            measurement_count += 1
            plt.title(f"Measurement {measurement_count}/{max_measurements}, press 'q' to stop")
    except KeyboardInterrupt:
        pass
    finally:
        plt.ioff()
        plt.close()

if __name__ == '__main__':
    main()
```
