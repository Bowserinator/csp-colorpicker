

# Clip Studio Paint: OkHSL/HSV Color Picker

![](https://github.com/Bowserinator/csp-colorpicker/blob/master/video.gif?raw=true)

A floating window OkHSL/OkHSV perceptual color picker for Clip Studio Paint. Based on <https://bottosson.github.io/posts/colorpicker/>. 

- This is an external pyQt program (always on top window) that remotely connects to CSP through its companion app API (only works for CSP, tested with CSP 4)
- The color picker can pick between OkHSV or OkHSL, which better match human perception of colors (ie, it better accounts for how pure blue appears darker than pure yellow)
- Window can be resized on the 5px margin on the bottom right (may not show cursor change)
- Window can be dragged from the bar at the top
- Recommended use: resize and move over where you normally have the color picker in CSP
- **Disclaimer:** Half of it (mainly the color picker) was vibecoded with manual bug fixing

## How does this work??

CSP has a companion app that among many things let you set the current color from your mobile device. The API was reverse engineered (simple socket server in python) + pyQT frontend that loads a webserver for the color picker.

## Installation

(Only required if you want to run the GUI app). For programmatic usage the client does not have any external dependencies.

```bash
git clone https://github.com/Bowserinator/csp-colorpicker.git && cd csp-colorpicker
pip install -r requirements.txt
```

## Running

> **Note:** A `config/` folder will be generated where you run it.

```bash
python3 main.py
```

Then open CSP, File > Connect to smartphone. Make sure the QR code is visible on screen, then click the button to load the QR code. It will screenshot your desktop and look for the companion app code automatically.

## Config

A `./config` folder is generated in the directory `main.py` is run in. Inside are 3 files:
- `web_data/`: For `localStorage` and other web data
- `.window.position`: Stores recent window size/position, saves every 30s
- `config.json`: **The main config file.**

```yaml
{
"port": 8765,                  # Server port
"host": "127.0.0.1",           # Server host to bind to
"web_base_dir": "...",         # Abs path to dir containing index.html
"client_refresh_rate_s": 0.05, # How often to sync, in seconds 
"auto_hide": true              # Auto minimizes window when csp not focused
}
```

Restart the app to see changes.

## Code Usage

`csp/csp_client.py` can be used standalone and does not have any external dependencies. The script can be run directly, there is a simple demo CLI program to interact with CSP in the file.
