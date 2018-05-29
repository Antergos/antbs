FROM antergos/archlinux-base-devel
MAINTAINER Antergos Developers <dev@antergos.com>
ADD antergos-mirrorlist /etc/pacman.d/antergos-mirrorlist
ADD http://repo.antergos.info/antergos/x86_64/antergos-keyring-20170524-1-any.pkg.tar.xz /
RUN pacman -U --noconfirm /antergos-keyring-20170524-1-any.pkg.tar.xz; echo "[multilib]" >> /etc/pacman.conf; \
echo "Include = /etc/pacman.d/mirrorlist" >> /etc/pacman.conf; \
sed -i 's%^keyserver hkp.+\n%keyserver hkp://keyserver.kjsl.com:80\n%g' /etc/pacman.d/gnupg/gpg.conf; \
echo "[antergos]" >> /etc/pacman.conf; echo "Include = /etc/pacman.d/antergos-mirrorlist" >> /etc/pacman.conf; \
pacman-key --init; pacman-key --populate archlinux antergos; \
pacman -Syyu --needed --noconfirm base-devel sudo yaourt openssh xdelta3 reflector expect devtools arch-install-scripts fakeroot; pacman -Scc --noconfirm
RUN echo 'antbs ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/10-builder
RUN useradd -r -U -s /usr/bin/nologin antbs && usermod -d /tmp/antbs -m antbs
