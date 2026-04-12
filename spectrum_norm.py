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