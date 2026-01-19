# Internxt Sync TUI

A Text User Interface (TUI) for synchronizing local folders with your Internxt Drive, built with [Textual](https://github.com/textualize/textual/).

This application provides a two-pane "Norton Commander" style interface to browse local and remote files and keep them in sync.

## Features

-   Dual-pane layout for local and remote file browsing.
-   Navigate local and remote filesystems.
-   Upload/Sync local folders to your Internxt drive.
-   Download files from Internxt to your local machine.
-   Handles sync logic: detects new, modified, and deleted files.
-   Interactive confirmation screen for remote deletions.
-   Editable path inputs for quick navigation.

## Installation

The application requires Python 3 and the Internxt CLI.

1.  **Install Internxt CLI**:
    Follow the official instructions: [https://github.com/internxt/cli](https://github.com/internxt/cli)

2.  **Install Python dependencies**:
    Clone the repository and install the required packages from `requirements.txt`. It is recommended to use a virtual environment.
    ```bash
    git clone https://github.com/Gabri/internxt-sync.git
    cd internxt-sync
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

## Usage

Run the application using the provided shell script:

```bash
./run.sh
```

The first time you run it, you may be prompted to log in to your Internxt account, which will open a browser window.

### Keybindings

| Key         | Action                  |
|-------------|-------------------------|
| `Tab`       | Switch between panes    |
| `s`         | Sync local to remote    |
| `d`         | Download selected remote file |
| `r`         | Refresh both panes      |
| `Ctrl+L`    | Focus the path input bar|
| `q`         | Quit the application    |

## License

This project is licensed under the **GNU Affero General Public License v3.0**. See the [LICENSE.md](LICENSE.md) file for details.

For commercial use that does not comply with the AGPLv3, please contact the author to arrange a commercial license.
