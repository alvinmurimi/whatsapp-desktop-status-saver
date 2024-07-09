# WhatsApp Desktop Status Saver

WhatsApp Status Saver is a cross-platform desktop application designed to seamlessly download and manage WhatsApp statuses.


## Background

As an avid WhatsApp user, I couldn't find an easy way to save statuses without emulators or complex workarounds, so I developed this simple, user-friendly app to do the job.

## Key Features

- **Cross-platform Support**: Works on both Windows and macOS
- **Intuitive UI**: Clean, responsive interface for easy navigation
- **Media Categorization**: Separate sections for photos and videos
- **Thumbnail Previews**: Quick visual browsing of available statuses
- **One-Click Save**: Effortlessly download statuses to your chosen directory
- **Theme Options**: Toggle between light and dark modes for comfortable viewing
- **Efficient File Management**: Delete saved statuses directly from the app
## Technical Stack

### Core Technologies

- **Python 3.7+**: The foundation of our application, chosen for its versatility and rich ecosystem.
- **Flet**: A framework for building interactive multi-platform applications using Flutter, enabling us to create a responsive and visually appealing UI with Python.
- **asyncio**: Utilized for handling asynchronous operations, ensuring smooth performance during file operations and UI updates.

### Key Libraries

- **Pillow (PIL Fork)**: Used for image processing tasks such as creating and manipulating thumbnails for photo statuses.
- **OpenCV (cv2)**: Employed for video processing, specifically for extracting thumbnail frames from video statuses.
- **os**: Handles file and directory operations across different operating systems.
- **shutil**: Used for high-level file operations, particularly for copying files during the download process.

## How It Works

1. **Status Discovery**: The app scans the WhatsApp desktop client's local storage directory where statuses are temporarily cached.
- Windows : ```%userprofile%\AppData\Local\Packages\5319275A.WhatsAppDesktop_cv1g1gvanyjgm\LocalState\shared\transfers```
- Mac: ```~/Library/Containers/net.whatsapp.WhatsApp/Data/Library/Application Support/WhatsApp/shared/transfers```

2. **File Categorization**: Statuses are sorted into photos and videos based on file extensions (Usually .JPG and .MP4).
3. **Thumbnail Generation**: 
   - For images: Pillow resizes the original image to create a thumbnail.
   - For videos: OpenCV extracts the first frame and processes it into a thumbnail.
4. **UI Rendering**: Flet is used to create a grid view of thumbnails, along with download/delete buttons for each status.
5. **Asynchronous Operations**: File downloads and deletions are handled asynchronously to prevent UI freezing.
6. **Local Storage**: Downloaded statuses are saved to a user-specified directory, with the default set to a 'WhatsappStatuses' folder in the user's Downloads directory.

## Installation

1. Clone this repository:
```git clone https://github.com/alvinmurimi/WhatsApp-Desktop-Status.git```

2. Install the required dependencies:
```pip install -r requirements.txt```

3. Run the application:
```python main.py``` or  ```flet run main.py```
## Requirements

- Python 3.7+
- Flet (0.23.2)
- Pillow (10.4.0)
- OpenCV Python (4.10.0.84)

  
For a complete list of dependencies, refer to the `requirements.txt` file.

## Compatibility

- **Windows**: Compatible with Windows 10 and 11
- **macOS**: Compatible with macOS 10.15 (Catalina) and later

Note: The WhatsApp desktop app must be installed and logged in on your computer for this application to work.

## Contributing

We welcome contributions! Please feel free to submit pull requests, report bugs, and suggest features through the GitHub issues page.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Disclaimer

This application is intended for personal use only. Users are responsible for respecting the privacy and copyright of content creators when saving and using WhatsApp statuses.
## Contributing

Contributions, issues, and feature requests are welcome! Feel free to check the [issues page](https://github.com/alvinmurimi/WhatsApp-Desktop-Status/issues).
