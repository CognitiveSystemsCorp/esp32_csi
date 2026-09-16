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
CSI_DATA,100,a0:36:bc:36:c7:38,-44,11,-92,11,12622117,14,0,128,1,"[0,0,0,0,0,0,0,0,0,0,2,-4,-8,39,-11,39,-14,38,-16,38,-18,37,-20,36,-21,34,-21,34,-22,32,-22,31,-21,31,-21,30,-20,30,-20,30,-18,29,-17,30,-15,29,-13,28,-13,28,-11,28,-10,26,-9,24,-7,23,-7,21,-6,20,-6,17,0,0,-7,12,-9,8,-10,6,-9,8,-10,6,-62,18,0,0,0,0,14,117,-24,-30,-110,1,0,0,64,76,-54,68,-91,-124,-72,19,0,0,0,0,23,117,-24,-30,-110,1,0,0,0,0,87,37,29,-58,-33,84,72,65,83,-23,96,-67,-119,-14,97,117]"
CSI_DATA,101,a0:36:bc:36:c7:38,-43,11,-92,11,12720300,14,0,128,1,"[0,0,0,0,0,0,0,0,0,3,-1,0,-14,-31,-11,-33,-8,-34,-5,-35,-3,-36,-1,-36,1,-35,3,-35,5,-35,6,-34,6,-33,7,-33,8,-33,8,-33,7,-32,7,-32,6,-31,5,-30,6,-30,5,-29,5,-27,5,-26,4,-24,5,-22,5,-20,6,-18,0,0,8,-12,10,-9,11,-6,10,-9,11,-6,-7,-12,-8,-15,-7,-12,-8,-15,-15,-23,-17,-22,-18,-22,-18,-23,-18,-24,-19,-23,-18,-24,-19,-23,-11,-13,-10,-11,-8,-9,-8,-8,-7,-7,-3,-7,-2,-7,0,-8,1,-7,-6,10,18,1,0,0,0,0,0,0]"
```

## Visualize

python `csi_data_parse_read.py -p PORT` can be used to visualize CSI data realtime

