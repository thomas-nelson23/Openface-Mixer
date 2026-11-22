# AUR package: openface-mixer-git

`PKGBUILD` builds the latest `main` as a system package: the commands in `/usr/bin`, the engine
units in `/usr/lib/systemd/user`, the udev rules in `/usr/lib/udev/rules.d`, the app in
`/usr/share/openface-mixer`. It runs `make install DESTDIR=… PREFIX=/usr`, the same installer as
`make install`, in its staging mode.

A packaged copy knows it is one: **Updates** in the settings says the package manager updates
it. Nothing is enabled at install time. The app enables a device's engine the first time it
sees that interface.

## Try it before publishing

```sh
cd packaging/aur
makepkg -si          # builds from GitHub main, installs it and any missing dependencies
```

If Openface Mixer is already installed with `make install`, remove that copy first with
`make uninstall` (mixes and presets stay). Otherwise both copies are installed, and the old
units in `~/.config/systemd/user` take precedence over the package's. Then open Openface Mixer
once with the interface connected, so its engine is enabled again.

## Publish (once)

1. Create an account on <https://aur.archlinux.org> and add your SSH public key under
   *My Account*.
2. Clone the package's (still empty) repository. Pushing to it creates the package:

   ```sh
   git -c init.defaultBranch=master clone ssh://aur@aur.archlinux.org/openface-mixer-git.git aur-openface-mixer-git
   ```

3. Copy `PKGBUILD` into it and put your name and the email you want AUR users to see in the
   `# Maintainer:` line.
4. In that folder, build and install once to check it, then write the metadata file from the
   PKGBUILD (it must be regenerated whenever the PKGBUILD changes):

   ```sh
   makepkg -si
   makepkg --printsrcinfo > .SRCINFO
   ```

5. Commit both files and push. The AUR only takes the `master` branch:

   ```sh
   git add PKGBUILD .SRCINFO
   git commit -m "Initial import"
   git push origin master
   ```

6. Add the install line to the main README: `yay -S openface-mixer-git` (or any AUR helper).

## Later

Users get the newest `main` whenever they rebuild; the version is worked out at build time
(`r<commits>.<hash>`, or `<tag>.r<commits since>.g<hash>` once there are release tags). Push to
the AUR again only when the recipe itself changes, for example a new dependency: edit `PKGBUILD`
here and there, raise `pkgrel`, regenerate `.SRCINFO`, commit and push.

The `.SRCINFO` in this folder was generated with `makepkg --printsrcinfo` and is only a
reference copy. The one in the AUR repository is what counts.
