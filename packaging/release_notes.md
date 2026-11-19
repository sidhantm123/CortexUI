CortexUI is a desktop app for viewing and analyzing multi-channel neural recordings from **TDT** and **Blackrock** systems.

## Which file?

| Your computer | Download |
|---|---|
| **Mac with Apple silicon** (Apple menu → About This Mac shows *Chip: Apple M…*) | `…-macOS-AppleSilicon.dmg` |
| **Mac with an Intel processor** (*Processor: … Intel …*) | `…-macOS-Intel.dmg` |
| **Windows 10 or 11** (64-bit) | `…-Windows-x64.zip` |

Macs need **macOS 14 (Sonoma) or later**.

## Install

**macOS**
1. Open the `.dmg` and drag **CortexUI** onto **Applications**.
2. Open CortexUI from Applications. The first time, macOS stops it because the app is not signed with an Apple developer account. Click **Done**.
3. Open **System Settings → Privacy & Security**, scroll down to the message about CortexUI, and click **Open Anyway**. Confirm with your password.

You only do this once. On macOS 14 you can instead Control-click the app and choose **Open**.

**Windows**
1. Right-click the `.zip` and choose **Extract All**.
2. In the extracted **CortexUI** folder, run **CortexUI.exe**. Keep it in that folder, next to `_internal`; to start it from elsewhere, create a shortcut.
3. If Windows shows *Windows protected your PC*, click **More info → Run anyway**. You only do this once.

## Updating

Install a new version the same way as the first time (on a Mac, replace the old app in Applications); your settings are kept. From version 1.1, CortexUI tells you when a new version is out (**Help → Check for Updates…**).

## Getting started

Choose **File → Open Recording Folder…** (or drop a folder on the window). Hover any setting to see what it does, or open **Help → Settings Guide** for all of them.

## Problems

Unexpected errors are written to `error.log`:
- **macOS:** `~/Library/Application Support/CortexUI/`
- **Windows:** `%APPDATA%\CortexUI\`

Please attach it when you [report a problem](https://github.com/sidhantm123/CortexUI/issues).
