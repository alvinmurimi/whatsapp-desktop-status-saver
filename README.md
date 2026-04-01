# WhatsApp Desktop Status Saver

WhatsApp Desktop Status Saver is a Python desktop app for browsing and saving WhatsApp statuses from a logged-in WhatsApp Desktop session.

<img src="screenshots/main.png" alt="Overview" width="550">

## What It Does

- Shows photo and video statuses in separate sections
- Lets you save statuses to a folder you choose
- Keeps downloaded items in a dedicated Downloads tab
- Generates thumbnails for quick browsing
- Supports light and dark mode

## How Status Discovery Works

Modern WhatsApp Desktop builds on Windows no longer reliably expose statuses through the old `LocalState\\shared\\transfers` folder alone.

This app now supports two discovery paths:

1. **Modern Windows path**
   Reads WhatsApp Desktop's WebView storage and extracts status media metadata from the IndexedDB message store.
2. **Legacy fallback**
   Falls back to the older temporary media folder approach when available.

On Windows, the app can:

- find current status message records from WhatsApp's WebView data
- resolve media URLs and cache the media locally
- generate thumbnails in the background
- keep a local status index cache so repeat launches are much faster

## Technical Stack

- **Python** for the application runtime
- **Flet** for the desktop UI
- **asyncio** for non-blocking UI tasks
- **Pillow** for image thumbnails
- **OpenCV** for video thumbnails
- **cryptography** for WhatsApp media decryption when needed
- **ccl_chromium_reader** for reading Chromium/WebView IndexedDB data on Windows

## Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/alvinmurimi/whatsapp-desktop-status-saver.git
   cd whatsapp-desktop-status-saver
   ```

2. Create and activate a virtual environment:

   ```powershell
   py -3.14 -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

3. Install runtime dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Launch the app:

   ```bash
   python main.py
   ```

   Or:

   ```bash
   flet run main.py
   ```

## Windows Executable Bundle

For users who do not want to install Python manually, this repository now includes a Windows bundle build path.

- Local build script: `build_windows_release.ps1`
- GitHub release workflow: `.github/workflows/windows-release.yml`

### Build Locally

1. Activate your virtual environment:

   ```powershell
   .\venv\Scripts\Activate.ps1
   ```

2. Run the Windows bundle script:

   ```powershell
   .\build_windows_release.ps1 -PythonExe .\venv\Scripts\python.exe -Version 0.1.0
   ```

3. Find the generated artifacts in:

   - `output\release\WhatsAppStatusSaver`
   - `output\release\WhatsAppStatusSaver-windows-x64.zip`

Users can extract the zip and run `WhatsAppStatusSaver.exe` directly without installing Python.

### Upload to GitHub

The simplest distribution path is a GitHub Release asset:

1. Push your changes and create a version tag.
2. Open the repository's **Releases** page on GitHub.
3. Draft a new release for that tag.
4. Upload `output\release\WhatsAppStatusSaver-windows-x64.zip` in the release assets area.
5. Publish the release.

The included workflow can also build the Windows bundle and attach the zip to a GitHub Release automatically.

## Requirements

- Python 3.10 to 3.14
- WhatsApp Desktop installed and logged in
- Windows 10 or Windows 11 for the modern WebView-backed status extraction flow

For the exact runtime dependency list, see [requirements.txt](requirements.txt).

## Compatibility

- **Windows**: Best-supported path. Modern WhatsApp Desktop builds are handled through WebView storage parsing.
- **macOS**: Legacy folder-based discovery may work depending on the installed WhatsApp build, but the newer Windows WebView extraction path is the primary supported implementation right now.

## Usage

1. Open WhatsApp Desktop and view the statuses you want available in the saver.
2. Start this app.
3. Browse Photos or Videos.
4. Click the save button on any item to copy it into your configured save folder.
5. Use the Downloads tab to review or delete items you already saved.

## Limitations

- The app depends on WhatsApp Desktop's current local storage behavior, which can change across releases.
- First-time indexing or cache rebuilds can be noticeably slower than later launches.
- If WhatsApp removes or changes local media/session storage formats again, the discovery code may need to be updated.

## Contributing

Issues, bug reports, and pull requests are welcome. You can open an issue at the [issues page](https://github.com/alvinmurimi/whatsapp-desktop-status-saver/issues).

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Disclaimer

This application is intended for personal use only. Respect the privacy, consent, and copyright expectations around any media you save.
