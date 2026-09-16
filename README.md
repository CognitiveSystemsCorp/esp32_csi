### Configure the project

```
git clone -b v6.0.2 --recursive https://github.com/espressif/esp-idf.git
cd esp-idf
./install.sh
./export.sh
cd ../
```

`idf.py set-target esp32c5`

Open the project configuration menu (`idf.py menuconfig`).

In the `Project Configuration` menu:

* Set the Wi-Fi configuration.
    * Set `WiFi SSID`.
    * Set `WiFi Password`.

### Build and Flash on 2x boards

Build the project and flash it to the board, then run the monitor tool to view the serial output:
Run `idf.py -p PORT flash monitor` to build, flash and monitor the project.

## Example Output
```
CSI_DATA,0,1,00:00:00:00:00:00,3c:dc:75:82:28:84,-73,11,-97,136,0,3973981,14,0,106,0,"[-40,14,-111,0,-48,14,-101,0,-28,14,-112,0,9,15,-121,0,51,15,-82,0,100,15,-52,0,-126,15,-15,0,-95,15,-16,0,-79,15,-25,0,-54,15,-52,0,-1,15,-70,0,52,0,-75,0,117,0,-24,0,105,0,9,1,74,0,13,1,21,0,-52,0,-4,15,109,0,9,0,6,0,75,0,-60,15,-95,0,-83,15,-23,0,-42,15,0,1,44,0,-46,0,120,0,118,0,-100,0,-5,15,119,0,-80,15,48,0,0,0]"
CSI_DATA,0,1,00:00:00:00:00:00,3c:dc:75:82:28:84,-74,11,-97,136,0,4044183,14,0,106,0,"[24,15,34,15,9,15,50,15,5,15,93,15,1,15,-116,15,-4,14,-37,15,-3,14,42,0,17,15,97,0,31,15,-114,0,79,15,-103,0,-125,15,-90,0,-55,15,-93,0,23,0,-85,0,106,0,-35,0,124,0,8,1,119,0,-7,0,62,0,-55,0,28,0,114,0,4,0,5,0,26,0,-103,15,69,0,76,15,-109,0,55,15,-37,0,81,15,-16,0,-100,15,-51,0,-15,15,114,0,52,0,11,0,107,0,0,0]"
```

## Visualize

pip install -r requirements.txt

Exit idf.py monitor by pressing ctrl+]

python3 `csi_data_parse_read.py -p PORT` can be used to visualize CSI data realtime

Run 2 instances, 1 for each unique PORT.

If project was flashed and run on 2 boards, each visualization instance would show 2 plots, 4 plots total. For each instance, one plot is for Access Point CSI, and one for Peer CSI. Peer is discovered automatically.

