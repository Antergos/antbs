FROM antergos/archlinux-base-devel
MAINTAINER Antergos Developers <dev@antergos.com>
ADD antergos-mirrorlist /etc/pacman.d/antergos-mirrorlist
ADD http://repo.antergos.info/antergos/x86_64/antergos-keyring-20140915-1-any.pkg.tar.xz /
RUN pacman -U --noconfirm /antergos-keyring-20140915-1-any.pkg.tar.xz; echo "[multilib]" >> /etc/pacman.conf; \
echo "Include = /etc/pacman.d/mirrorlist" >> /etc/pacman.conf; \
sed -i 's%^keyserver hkp.+\n%keyserver hkp://keyserver.kjsl.com:80\n%g' /etc/pacman.d/gnupg/gpg.conf; \
echo "[antergos]" >> /etc/pacman.conf; echo "Include = /etc/pacman.d/antergos-mirrorlist" >> /etc/pacman.conf; \
pacman-key --init; pacman-key --populate archlinux antergos; \
pacman -Syy --needed --noconfirm sudo yaourt openssh xdelta3 expect devtools arch-install-scripts; pacman -Scc --noconfirm
ADD http://sourceforge.net/projects/expect-lite/files/latest/download /tmp/
RUN cd /tmp; tar -xvf download; cd expect-lite.proj; ./install.sh
RUN echo 'antbs ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/10-builder
RUN echo 'antbs:x:1001:100:antbs:/:/usr/bin/nologin' >> /etc/passwd
