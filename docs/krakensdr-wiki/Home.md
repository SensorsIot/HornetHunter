<p align="center">
<img src="https://user-images.githubusercontent.com/78108016/170861942-ca473872-7b85-4c8c-ab87-9bebba5c9f95.png" width="1000"/>
</p>
<p align="center">
<img src="https://user-images.githubusercontent.com/78108016/170988033-4dfd93eb-8f1e-4f2c-a943-f7a0723b557a.png"/>
</p>

A coherent radio allows for very interesting applications, such as radio direction finding, and beamforming. Some use cases include:

* Physically locating an unknown transmitter of interest (e.g. illegal or interfering broadcasts, noise transmissions, or just as a curiosity)
* HAM radio experiments such as radio fox hunts or monitoring repeater abuse
* Tracking assets, wildlife, or domestic animals outside of network coverage through the use of low power beacons
* Locating emergency beacons for search-and-rescue teams
* Locating lost ships via VHF radio
* Beamforming
* Interferometry for radio astronomy

# KrakenSDR vs KerberosSDR
KrakenSDR is the upgraded version of KerberosSDR. The main new feature is that with KrakenSDR calibration is entirely automatic. Unlike with the KerberosSDR where calibration requires that the antennas be disconnected manually each time.

KrakenSDR also has 5-channels vs 4-channel on the KerberosSDR. The additional channel improves bearing accuracy.

The entire design is also improved with USB-C used, an improved low noise design, bias tees, improved cooling, a more rugged enclosure, and improved ESD protection.

# KrakenSDR Software
This Wiki details the use of our software stack. Our software consists of the core DAQ and DSP software which is designed to run on a Raspberry Pi 4 or similar single board computer that is connected to the KrakenSDR. The second layer of software is an Android App which is used to plot and map the bearing data produced by the core system.

The KrakenSDR software is also compatible with KerberosSDR units. Users will only need to change the configuration file. However, KerberosSDR users will still need to manually disconnect antennas during a calibration cycle (or purchase a third party switch upgrade).

For a quick way to get started with KrakenSDR and a Pi 4, consult our [https://github.com/krakenrf/krakensdr_docs/wiki/02.-Direction-Finding-Quickstart-Guide](Quick Start Guide) on this wiki.

Links to our software repos:

Heimdall DAQ - https://github.com/krakenrf/heimdall_daq_fw/

KrakenSDR Direction Finding - https://github.com/krakenrf/krakensdr_doa

KrakenSDR GNU Radio Block - https://github.com/krakenrf/gr-krakensdr

# Videos

**KrakenSDR Direction Finding in a Vehicle:** https://www.youtube.com/watch?v=OY16y1Rl86g

# Technical Specifications

* Dimensions: L: 177mm x W: 112.3mm x H: 25.86mm (+4.7mm height for fan finger guard) (See appendix for drawing)
* Weight: 670g
* Typical Power Consumption: 5v, 2.2A (11W)
* Radio Tuner: 5x R820T2
* Radio ADC: 5x RTL2832U
* ADC Bit Depth: 8-bits
* Frequency Range: 24 MHz -1766 MHz
* Maximum Channel Bandwidth: 2.56 MHz
* RX Channels: 5
* Oscillator Stability: 1PPM

# KrakenSDR Hardware Descriptions

![side](https://user-images.githubusercontent.com/78108016/169803278-2ec366e6-40e5-4ac8-a9a0-f91b6deebb16.png)
![TOP](https://user-images.githubusercontent.com/78108016/169803288-50f3b191-4710-47cd-abc8-377bbac2945c.png)

## KrakenSDR Power Port
The KrakenSDR requires DC power from a 5V 2.4A capable USB-C power supply. It is also compatible with ‘PD’ type USB-C power packs that you might find used with laptops.

For use in vehicles, it is possible to use USB cigarette lighter adapters. Make sure that they can support at least 5V 2.4A out. Battery packs can also be used if they support 2.4A or more output current.

Bias Tee Note: The KrakenSDR draws 2.2A under nominal operation. If you intend to use the bias tees to power external devices, please ensure that you use a power pack capable of providing the power required. For example, if you are using a 3A capable power pack, you will have about 800 mA of current margin.

## KrakenSDR Data Port
The KrakenSDR requires a USB-C cable to connect the computing device to the data port. Please note that this data port is not connected to power, so you cannot power the device from the data cable. You must use the data cable AND the power cable together.
Make sure that you use a high-quality USB-C cable, no longer than 1m in length.  
 
## KrakenSDR Cooling
The KrakenSDR is passively cooled by heatsink fins and actively using a cooling fan. Internally the PCB is thermally connected to the cooled enclosure using thermally conductive silicon.

KrakenSDR has been tested to operate normally in ambient temperatures of up to 50C / 122F.

We recommend keeping your KrakenSDR out of direct sunlight and in an area with sufficient air flow.

Large sudden temperature swings may cause issues with phase coherence calibration being lost. 

## SMA Ports
The KrakenSDR has 5 SMA RX IN ports for connecting antennas labelled from CH0 to CH4.

## Bias Tee
The KrakenSDR can provide 4.5V DC via a bias tee on each of its SMA ports. This can be used for powering external RF components such as an LNA. Your power pack must be able to provide sufficient current to support external devices.
 
## Status Lights
The KrakenSDR provides peep holes for several status LED indicators.

**PWR LED:** The white PWR LED to the right of the two USB-C ports indicates that the KrakenSDR is receiving DC power from the POWER USB-C port.

**NOISE LED:** When illuminated the white NOISE LED to the right of the PWR LED indicates that the noise source on the KrakenSDR is active. This LED may flash briefly every few minutes when running the software when calibration monitoring/auto recalibration is turned on.

**CHANNEL LEDS:** There are five blue CHANNEL LEDS next to each of the SMA ports. When illuminated, these LEDs indicate that this channels tuner has been enumerated by the computing device. Please note that these LEDs do not provide status regarding the channel connection to the DSP software.

# KrakenSDR Design

The KrakenSDR coherent design consists of:

* 5x RTL-SDR tuners (with R820T and RTL2832U chips)
* 1x single clock source for all RTL-SDRs
* 1x noise source
* 5x noise / antenna port switches
* 1x USB hub

The KrakenSDR is not a naturally coherent system just by its SDR hardware alone. However, its design with a single clock source and software activatable noise source with switches, allows for phase coherence to be achieved in software through implementing correlation algorithms that synchronize samples and phase between the receivers. 

Once the software has booted, the noise source will be activated, and each channel automatically correlated against the master channel (CH0 by default). Any sample timing and phase differences between the channels will be determined, and each sample’s timing and phase will be automatically adjusted in software. This results in the coherent operation of all five channels.

# Caring for your KrakenSDR

## Nearby Transmitters
The KrakenSDR is a sensitive radio receiver. Like most radio receivers, any antennas connected to the KrakenSDR MUST be kept away from powerful nearby RF transmitters. 

The maximum input power allowed at the SMA receiver ports is +10dBm. Please ensure external measures like filtering, switching or physical placement are in place to block or limit RF power to the SDR if you know that you will be operating close by to a powerful transmitter.

## Lightning and ESD Damage
For protection, the KrakenSDR implements an ESD diode, gas discharge tube, and diode power clipper. 
However, it will not withstand direct or induced electrical surges from nearby lightning events, or huge electrostatic discharge (ESD) events that are possible from events like snow and dust storms.

Therefore, we suggest that any outdoor connected antenna MUST have externally provided lightning and ESD protection measures in place.

## Operating Environment
The KrakenSDR has been tested to operate normally in ambient environments of up to 50C. However, for longevity it is recommended to keep it in a cool environment, and out of direct sunlight.

# Safety and Environment

Before getting started with your KrakenSDR, please review these safety instructions.

**Electrical shock:** You could be INJURED OR KILLED if live electrical wires touch an antenna that is connected to a KrakenSDR. When dealing with external antennas, always ensure that your antennas are kept well away from power lines and any other live electrical wires.

**EM leakage:** The KrakenSDR CANNOT TRANSMIT RF, however, it does contain a built-in internal wideband noise source that is used for phase calibration purposes. Although this noise source is low power, isolated from the antennas via a high isolation silicon switch and enclosed within a metal faraday cage, there could be small amounts of wideband EM noise leakage that could interfere with highly sensitive radio equipment. This leakage has been measured to be well below regulatory compliance thresholds. However, should you open or remove the enclosure or make any modifications to the unit for any reason, noise source leakage may increase beyond compliance thresholds.

**Enclosure temperature:** The KrakenSDR metal enclosure may become warm or hot to the touch during operation. The KrakenSDR should be kept out of direct sunlight and placed in a ventilated space.

**Cooling fan blades:** The KrakenSDR contains a cooling fan on the enclosure that operates at high RPM. A finger guard is fitted, however small fingers and debris could get into the fan blades. Please take care when handling an operating unit, and always ensure the fan area is clear of debris before powering on the device.

**Driving risks:** A typical use-case for the KrakenSDR may be using it in a motor vehicle for radio direction finding. Please, always pay attention to the road when using the device in a vehicle and only have a passenger operate the KrakenSDR and perform all navigation tasks. Always ensure the antennas on the roof of your vehicle are attached securely and comply with all local road laws.

**Compliance with Law:** It is the sole responsibility of the KrakenSDR owner / user to comply with all laws that apply in their jurisdiction. Specifically, around any licence fees applicable to receivers, information that can be legally extracted from the RF environment, making RF environment observations public, or not using the KrakenSDR for any illegal purpose.  This may include information gained from the remote operation from the KrakenSDR.

**Regions of conflict/war or use in sensitive locations:** You may be risking your life, or arrest, when using any sort of radio receiver in regions of conflict/war or in sensitive locations. Please consider your need to use the KrakenSDR very carefully in these areas.

# Recycling

The KrakenSDR is compliant with RoHs. However, as the KrakenSDR contains a PCB and electronic components, please do not dispose of it in the household trash. Should you need to dispose of a KrakenSDR, please take it to an e-waste recycling plant, or ship it back to KrakenRF Inc. Our details can be found at krakenrf.com.

# Warranty
The KrakenSDR has a one-year warranty from manufacturing defects.

Please keep in mind the warranty will not cover damage from external events such as lightning or ESD, fire, over voltage, flooding, physical damage, or modifications. 

The warranty will be voided if the KrakenSDR enclosure is opened.

If you have a warranty claim, please contact us at thekraken@krakenrf.com.