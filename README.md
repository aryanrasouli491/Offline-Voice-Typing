# Voice Typing Offline

A Windows desktop speech-to-text application that runs locally using Faster-Whisper Small. The optional FastAPI backend is included for local integrations. The compact interface keeps the microphone controls visible at the bottom, lets the transcript editor resize with the window, and includes day/night themes and quick model controls. A custom `app.ico` is included for the window/taskbar icon.

> **Language note:** In my testing, English transcription is much more accurate. Dari (Afghan Persian) support is currently weak and may contain many errors. This project should be considered English-first; please test Dari carefully before depending on it.

## Quick start on Windows

1. Download this repository using **Code → Download ZIP** and extract the ZIP to a normal folder, for example `C:\Voice-Typing-Offline`.
2. Double-click `Install_Voice_Typing.bat`.
3. Keep the window open while Python (if needed), libraries, and the model are installed. A stable internet connection and about 3 GB of free disk space are recommended.
4. When installation finishes, the desktop app opens automatically.
5. Next time, double-click `Start Voice Typing.vbs` to open the app without a Command Prompt window. `Run_Voice_Typing.bat` is an alternate launcher. Start/stop recording controls stay visible in the bottom bar, even when you resize the app window.

The setup script uses an existing Python 3.11, 3.12, or 3.13 installation when available. Otherwise, it downloads the official Python 3.13.16 Windows installer, checks its digital signature, and installs it for the current Windows user. It does not disable Windows SmartScreen.

## Downloads and model storage

The installer downloads required Python packages and the public model **`Systran/faster-whisper-small`** from Hugging Face. The model is stored at `models/small`, which is used by the desktop app and optional backend. Model weights are intentionally not committed to GitHub, so the code repository stays small and GitHub's ordinary 100 MB per-file repository limit is not a problem.

An internet connection is required for the first installation. After the model and packages are installed, the desktop speech-recognition operation runs locally and can work offline. Re-running the installer checks the model and can retry an incomplete download.

## Optional local API backend

The desktop app can run without the backend. To start the local API service after installation, double-click `Run_Backend.bat`. It listens on `127.0.0.1:8765` and uses a local token stored in `backend/local_config.json`.

The installer generates a random token separately on each computer. **Do not commit `backend/local_config.json` to GitHub or share it.** The `.gitignore` file excludes it. The example config contains no real token.

## Troubleshooting

- **Python or package setup failed:** run `Install_Voice_Typing.bat` again and inspect `install-pip.log`.
- **Model download failed:** check access to `huggingface.co`, then rerun the installer. The Hugging Face download can often reuse cached files.
- **The window opens and closes immediately:** run `Run_Voice_Typing.bat` from the project folder to see terminal errors, and check `data/app.log`.
- **PyAV/Faster-Whisper decoding error:** run `repair_desktop_pyav.bat`.
- **No model appears in the app:** confirm that `models/small/model.bin`, `models/small/config.json`, `models/small/tokenizer.json`, and `models/small/vocabulary.txt` exist, then press the model refresh button if available.

## Hardware and disk space

The app uses CPU inference with INT8 quantization by default, so a dedicated NVIDIA GPU is not required. Speed depends on the CPU, recording length, and available memory. For the most comfortable experience, use a 64-bit Windows computer with at least 8 GB RAM and several GB of free storage; 16 GB RAM is preferable.

## License and model credit

- No source-code license has been selected yet. Before inviting contributions or granting broad reuse rights, choose and add a license (for example, MIT if you want to allow broad reuse).
- The upstream Faster-Whisper Small model is published by [Systran on Hugging Face](https://huggingface.co/Systran/faster-whisper-small) under its listed model license. Check that page for model-specific terms and attribution.
- Python and third-party packages retain their own licenses.

## Uploading this project to your own GitHub account

1. Create a new **public** repository on GitHub.
2. Upload the files in this project folder, or use GitHub Desktop to publish the folder.
3. Do not upload the `.venv` folder, the `models/small` model files, `data` contents, logs, or `backend/local_config.json`.
4. Keep `.gitignore` in the repository.
5. Copy the repository link into your project description. Users can download the source ZIP and run the installer.
6. Optional: after the repository is public, edit `DOWNLOAD_AND_INSTALL_FROM_GITHUB_TEMPLATE.bat` and replace `YOUR-USERNAME/voice-typing-offline` with the exact owner/repository name. Confirm that the default branch is `main`. Commit and publish the edited file. This template can then download the latest source from GitHub and launch the full installer. Do not distribute it with the placeholder unchanged.

Git is the version-control tool; GitHub hosts the repository. The model download comes from Hugging Face, so you do not need to own a server or pay to host the model yourself.
