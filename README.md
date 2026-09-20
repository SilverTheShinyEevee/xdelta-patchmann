Xdelta Patch Manager

An interactive, cross-platform Python tool for creating and applying Xdelta 3 patches without having to manually install, locate, configure, or troubleshoot Xdelta.

It works on Windows, macOS, Linux, and Android/Termux, supports both 32-bit and 64-bit environments where the underlying Xdelta build is available, and uses only Python's standard library.

The program is deliberately interactive: it detects your environment, verifies that Xdelta actually works, guides you through file selection, shows the command it is about to execute, and asks for confirmation before making changes.

Features

Create Xdelta patches

Select an original/base file.

Select the modified version.

Choose where to save the resulting .xdelta patch.

Configure secondary compression and compression level.

Apply Xdelta patches

Select the original/base file.

Select an Xdelta patch.

Choose the output filename.

Performs basic format detection before running Xdelta.

Automatic Xdelta detection

Searches for existing xdelta3/xdelta installations.

Checks the reported version.

Runs a real encode/decode round-trip self-test.

Tests LZMA support separately.

Automatic installation

Uses the detected operating-system package manager when possible.

Falls back to the official Xdelta project on GitHub.

Uses an official prebuilt release when one is available.

Otherwise downloads the official source release and builds Xdelta locally with CMake.

Cross-platform support

Windows

macOS

Linux

Android through Termux

Documentation support

Detects mandoc/man where available.

Installs a documentation viewer when appropriate.

Can display Xdelta's manual page or built-in help.

Provides access to the online Xdelta documentation.

Path handling

Full or relative paths.

~ and environment variables.

Quoted paths.

Drag-and-drop paths from supported terminals/shells.

Windows drive roots and network paths.

Android shared storage guidance.

Safety checks

Shows the selected files and options before execution.

Displays the exact Xdelta command before running it.

Requires confirmation before executing the operation.

Warns when an existing output file will be overwritten.

Never invokes commands through a shell.

Rejects unsupported Xdelta options.

Permanently refuses Xdelta's -n checksum-disabling option.

Why this exists

Xdelta itself is a command-line tool, but getting from "I have two files" to "I have a working Xdelta installation and a correctly constructed patch command" can involve quite a few platform-specific details.

This project puts those details behind an interactive interface.

Instead of having to remember commands such as:

xdelta3 -e -s original.bin modified.bin patch.xdelta 

or:

xdelta3 -d -s original.bin patch.xdelta restored.bin 

you can simply start the program and follow the prompts.

The tool also verifies the Xdelta executable with an actual encode/decode test rather than assuming that finding an executable named xdelta3 means it is usable.

Requirements

Python 3.7 or newer

Internet access may be required when:

Xdelta is not already installed.

A newer Xdelta release is being installed.

build dependencies need to be installed.

Xdelta needs to be built from source.

No third-party Python packages are required.

The Python script uses only the standard library.

Installation

There is no Python package installation step.

Download xdelta_patch_manager.py and run it with Python:

python3 xdelta_patch_manager.py 

On Windows:

python xdelta_patch_manager.py 

The program does not require command-line arguments.

First run

On startup, the program:

Detects the operating system and CPU architecture.

Detects an available package manager.

Displays useful filesystem/path information.

Looks for an existing Xdelta installation.

Verifies candidate Xdelta executables with a real self-test.

Offers to install Xdelta if no working copy is found.

Detects or installs a documentation viewer.

Checks for updates when appropriate.

Presents the main menu.

The main menu provides:

1. Create a patch 2. Apply a patch 3. Documentation 4. Check for updates q. Quit 

Creating a patch

Choose Create a patch, then provide:

the original/base file;

the modified file;

the output .xdelta filename.

For example:

Original: game-original.gba Modified: game-modified.gba Patch: game-modification.xdelta 

The tool then lets you choose the secondary compression method and compression level.

Before Xdelta is executed, the program displays the complete command and asks for confirmation.

Conceptually, the resulting command will look like:

xdelta3 -e -s game-original.gba game-modified.gba game-modification.xdelta 

The exact command depends on the options selected.

Applying a patch

Choose Apply a patch, then provide:

the original/base file;

the .xdelta patch;

the output filename.

For example:

Original: game-original.gba Patch: game-modification.xdelta Output: game-modified.gba 

The generated command will be equivalent to:

xdelta3 -d -s game-original.gba game-modification.xdelta game-modified.gba 

The tool checks for common mistakes and reports useful hints when Xdelta returns an error.

Checksum protection

One of the project's deliberate design decisions is that Xdelta's checksum-disabling -n option is never permitted.

The option is rejected even when disguised as part of a combined switch, such as:

-vn -fn 

Long options are also rejected.

The reason is that the program is intended to make patch creation and application safer for interactive users. Checksums provide an important validation mechanism, particularly when applying patches to files that may differ from the exact base file expected by the patch.

The tool therefore always reports:

Checksums : ON (this tool never disables them) 

There is also a second validation pass immediately before the command is executed.

Advanced Xdelta options

Advanced users can provide additional Xdelta switches.

For example:

-B 268435456 -W 16777216 -v 

The tool parses and validates these switches rather than passing arbitrary command-line text directly to a shell.

Some options are deliberately reserved because the manager supplies them itself or because they would conflict with its workflow.

Examples include:

-n — refused; checksum disabling is not allowed.

-e — supplied automatically when creating patches.

-d — supplied automatically when applying patches.

-s — supplied automatically from the selected files.

-c — refused because it bypasses the managed output file.

-J — refused because it produces no output file.

-h / -V — use the Documentation menu instead.

long options such as --example — refused.

Xdelta installation

The manager first tries to use a supported package manager.

Windows

Possible package managers include:

winget

Chocolatey

If no usable package-manager installation is available, the program can fall back to the official Xdelta project.

macOS

Homebrew is supported.

The program also understands both Intel and Apple Silicon environments and can use the appropriate official release where available.

Linux

Supported package managers include:

apt-get

dnf

yum

pacman

zypper

apk

xbps-install

If no usable package-manager installation is available, the program can download the official source release and build it locally.

Android / Termux

Termux's pkg package manager is supported.

The program can also detect when Termux has not yet been granted access to Android shared storage and offers to run:

termux-setup-storage 

This allows files in locations such as:

/storage/emulated/0/Download/ 

to be used by the program.

Managed installation

When the program installs Xdelta itself rather than relying on a system installation, it keeps the files under:

~/.xdelta-patch-manager/ 

The main locations are:

~/.xdelta-patch-manager/bin/ ~/.xdelta-patch-manager/share/man/ ~/.xdelta-patch-manager/installed.json 

This avoids requiring the manager's downloaded/built Xdelta executable to be installed system-wide.

Existing system Xdelta installations take precedence over the managed copy when appropriate.

Verification

Finding an executable is not considered sufficient.

For every candidate Xdelta executable, the manager:

Runs its version/help output.

Confirms that it appears to be Xdelta 3.

Creates a temporary test input.

Creates a modified version.

Encodes the difference.

Decodes the patch.

Compares the decoded output with the expected data.

Tests LZMA compression separately.

A candidate that fails the round trip is rejected.

Temporary self-test/build files are removed after use.

Release downloads and checksums

When downloading official GitHub release assets, the manager uses the release metadata published by the Xdelta project.

If GitHub publishes a SHA-256 digest for the selected asset, the downloaded file is verified against it. A mismatch causes the download to be discarded.

If no checksum is published for an asset, the program reports that fact rather than pretending that a checksum was verified.

Building Xdelta from source

When no suitable official prebuilt binary is available, the manager can build Xdelta from its official source release.

The process uses CMake and the platform's native compiler/build environment.

Depending on the platform, it may install required build dependencies through the detected package manager.

The resulting executable is installed into the manager's private directory and subjected to the same Xdelta identification and encode/decode self-test as a downloaded binary.

Supported architectures

The manager recognizes common:

x86 / 32-bit Intel

x86_64 / AMD64

ARM32

ARM64 / AArch64

It also accounts for cases such as:

32-bit Python on 64-bit Windows;

macOS running under Rosetta on Apple Silicon;

32-bit ARM Android userlands.

Actual Xdelta availability still depends on the release binaries and build environment available for the particular platform.

Error handling

The program attempts to provide actionable diagnostics rather than simply printing an Xdelta exit code.

For example, when applying a patch, it can point out common causes such as:

the base file is not the exact version expected by the patch;

the patch is damaged;

the selected file is not an Xdelta patch;

the Xdelta build lacks LZMA support;

the output could not be created;

the process may have run out of memory.

An output file left behind after a failed operation can be offered for deletion.

Patch format detection

Before applying a patch, the manager performs a small amount of signature detection.

It can recognize common signatures associated with:

Xdelta/VCDIFF

IPS

BPS

UPS

some compressed files

This does not replace Xdelta's own validation. It is primarily intended to catch obvious cases where a user selected a patch produced by another patching system.

Security considerations

The program takes several precautions when handling external commands and downloaded archives.

External commands are executed without a shell.

User-entered switch text is parsed and validated.

Unsupported/unknown Xdelta switches are rejected.

The checksum-disabling -n option is permanently rejected.

Downloaded release assets can be SHA-256 verified when GitHub provides a digest.

Source archives use guarded extraction logic on Python versions without the newer tarfile data filter.

Temporary download/build/test directories are removed after use.

Managed binaries are installed using a temporary file followed by an atomic replacement.

The user is shown the command before it executes.

These measures do not make the program or Xdelta itself risk-free. Users should still obtain patches and input files from sources they trust.

Data and network access

The manager does not require a server or account.

Network access is used only when needed for tasks such as:

querying the official Xdelta GitHub release;

downloading an official release asset;

downloading an official source archive;

accessing the online documentation;

opening the documentation in a browser at the user's request.

The program stores its own installation metadata locally in:

~/.xdelta-patch-manager/installed.json 

Project structure

The project can remain a single-file application:

xdelta-patch-manager/ └── xdelta_patch_manager.py 

This is intentional. The script has no Python dependency installation step and can be copied to another machine and run directly with Python.

Limitations

Xdelta must ultimately be available as a working executable; automatic installation depends on the platform, package repositories, GitHub releases, and available build tools.

Building from source can require several additional packages and may take some time.

Some architectures do not have official prebuilt Xdelta binaries and therefore require a local build.

The program only manages Xdelta patch creation/application; it does not convert between IPS, BPS, UPS, and Xdelta formats.

Applying an Xdelta patch still requires the exact base data expected by that patch.

Package-manager behavior varies between operating systems and distributions.

Official Xdelta project

Xdelta is developed by Joshua MacDonald and is available from the official project repository:

https://github.com/jmacd/xdelta

The Xdelta command-line documentation is available at:

https://jmacd.github.io/xdelta/commandline/

This project is a separate utility that manages installation, verification, and interactive use of Xdelta.

License

Choose and add the license appropriate for this project's source code.

If the project is intended to be distributed publicly, a standard OSI-approved license such as MIT, BSD-2-Clause, or Apache-2.0 can be added as a separate LICENSE file.

Quick start

python3 xdelta_patch_manager.py 

Then choose:

1. Create a patch 

or:

2. Apply a patch 

The manager handles the rest interactively.