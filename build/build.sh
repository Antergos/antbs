#!/bin/bash
# -*- coding: utf-8 -*-
#
#  build.sh
#
#  Copyright © 2014-2015 Antergos
#
#  This file is part of The Antergos Build Server, (AntBS).
#
#  AntBS is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2 of the License, or
#  (at your option) any later version.
#
#  AntBS is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Cnchi; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#  MA 02110-1301, USA.
pkg_deps="$*"



###=====================================================================================================
##
#      HELPER FUNCTIONS
##
###=====================================================================================================


function print2log() {

	echo '[\^/\^/^\^/^\^/] ' "${1}" ' [\^/\^/^\^/^\^/]'

}


function setup_environment() {

	update_error='ERROR UPDATING STAGING REPO (BUILD FAILED)'
	update_success='STAGING REPO UPDATE COMPLETE'
	mkdir /var/cache/pacman/success
	export HOME=/root
	export _MKPKG_OPTS='-smfL --noconfirm --noprogressbar --needed'
	echo "${_MKPKG_OPTS}" > /dev/null

	if [[ -f /pkg/PKGBUILD ]]; then

		source /pkg/PKGBUILD && export PKGNAME="${pkgname}"
		chmod -R a+rw /pkg
		cd /pkg && git pull

	elif [[ "${_UPDREPO}" != "True" ]]; then

		print2log 'ERROR WHILE SETTING UP ENVIRONMENT (BUILD FAILED)'
		exit 1;

	fi

	if [[ ${_ALEXPKG} = False ]]; then

		echo "GPGKEY=24B445614FAC071891EDCE49CDBD406AA1AA7A1D" >> /etc/makepkg.conf
		export PACKAGER="Antergos Build Server <dev@antergos.com>"
		sed -i 's|#PACKAGER="John Doe <john@doe.com>"|PACKAGER="Antergos Build Server <dev@antergos.com>"|g' /etc/makepkg.conf
		sed -i '1s%^%[antergos-staging]\nSigLevel = Never\nServer = http://repo.antergos.info/$repo/$arch\n%' /etc/pacman.conf
		sed -i 's|Include = /etc/pacman.d/antergos-mirrorlist|Server = http://repo.antergos.info/$repo/$arch\n|g' /etc/pacman.conf

	else

		export PACKAGER="Alexandre Filgueira <alexfilgueira@cinnarch.com>"
		sed -i 's|#PACKAGER="John Doe <john@doe.com>"|PACKAGER="Alexandre Filgueira <alexfilgueira@cinnarch.com>"|g' /etc/makepkg.conf
		sed -i '/\[antergos/,+1 d' /etc/pacman.conf
		sed -i '/\[antergos-staging/,+1 d' /etc/pacman.conf
		sed -i '1s%^%[antergos-staging]\nSigLevel = Never\nServer = http://repo.antergos.info/$repo/$arch\n%' /etc/pacman.conf
		#sed -i '/\[antergos-staging/,+1 d' /etc/pacman.conf

	fi

	sed -i 's|CheckSpace||g' /etc/pacman.conf
	sed -i '/CFLAGS=/c\CFLAGS="-march=native -mtune=generic -O2 -pipe -fstack-protector-strong --param=ssp-buffer-size=4"' /etc/makepkg.conf
	sed -i '/CXXFLAGS=/c\CXXFLAGS="-march=native -mtune=generic -O2 -pipe -fstack-protector-strong --param=ssp-buffer-size=4"' /etc/makepkg.conf
	sed -i '/#MAKEFLAGS=/c\MAKEFLAGS="-j3"' /etc/makepkg.conf

}


function in_array() {

	local e
	for e in "${@:2}"; do
		[[ "$e" == "$1" ]] && return 0;
	done

	return 1

}


function run_update_repo() {

	print2log "UPDATING ${1} REPO";

	for arc in i686 x86_64; do
		cd "/${repo_dir}/${arc}"
		repo-add -R -f "${repo}.db.tar.gz" ./*.xz
	done && touch "/result/${PKGNAME}" && return 0;

	return 1

}


function run_remove_pkg() {

	local repo_dir=staging
	local repo=antergos-staging
	print2log "REMOVING ${1} FROM STAGING REPO";

	for arc in i686 x86_64; do
		cd "/${repo_dir}/${arc}"
		repo-remove "${repo}.db.tar.gz" "${1}"
		rm "/${repo_dir}/${arc}/${PKGNAME}"***
	done && return 0

	return 1

}

function try_install_deps() {

	print2log 'TRY BUILD FAILED. TRYING TO INSTALL MISSING DEPS'
	cd /staging

	for dep in ${pkg_deps[*]}; do
		print2log "INSTALLING ${dep} AS DEP";
		yaourt -Sa --noconfirm --nocolor --noprogressbar --needed "${dep}" 2>&1;
	done && return 0;

	return 1
}

function copy_any() {

	for file in "/${repo_dir}/x86_64/${PKGNAME}"*-any.**.xz; do
		if [[ -f ${file} ]]; then
			cp "${file}" /staging/i686/
		fi
	done && run_remove_pkg "${PKGNAME}" && return 0;

	return 1;

}

function check_pkg_sums() {

	if [[ ${_AUTOSUMS} != "True" ]]; then
		if [[ ${1} = '' ]]; then
			sudo -u antbs /usr/bin/updpkgsums 2>&1 && return 0
		else
			arch-chroot /32build/root /usr/bin/bash -c "cd /pkg; sudo -u antbs /usr/bin/updpkgsums" 2>&1 && return 0;
		fi
	else
		return 0
	fi

	return 1

}

function build_32bit_pkg() {

	print2log 'BUILDING i686 PACKAGE';
	chmod -R 777 /32build
	chmod -R a+rw /staging/i686
	cp /usr/share/devtools/makepkg-i686.conf /32bit/makepkg.conf
	cp /etc/pacman.conf /32bit

	if [[ ${_ALEXPKG} = False ]]; then

		echo "GPGKEY=24B445614FAC071891EDCE49CDBD406AA1AA7A1D" >> /32bit/makepkg.conf
		sed -i 's|#PACKAGER="John Doe <john@doe.com>"|PACKAGER="Antergos Build Server <dev@antergos.com>"|g' /32bit/makepkg.conf
		cd /32bit

		if ! grep -Rl 'antergos-staging'; then

			sed -i '1s%^%[antergos-staging]\nSigLevel = Never\nServer = http://repo.antergos.info/$repo/$arch\n%' /32bit/pacman.conf
		fi

		sed -i 's|Include = /etc/pacman.d/antergos-mirrorlist|Server = http://repo.antergos.info/$repo/$arch\n|g' /32bit/pacman.conf
		export _MKPKG_OPTS='-smfL --noconfirm --noprogressbar --needed'

	else

		sed -i '/\[antergos/,+1 d' /32bit/pacman.conf
		sed -i '/\[antergos-staging/,+1 d' /32bit/pacman.conf
		cd /32bit
		sed -i '1s%^%[antergos-staging]\nSigLevel = Never\nServer = http://repo.antergos.info/$repo/$arch\n%' /32bit/pacman.conf
		sed -i 's|#PACKAGER="John Doe <john@doe.com>"|PACKAGER="Alexandre Filgueira <alexfilgueira@cinnarch.com>"|g' /32bit/makepkg.conf
		export _MKPKG_OPTS='-smfL --noconfirm --noprogressbar --needed'

	fi

	echo "${_MKPKG_OPTS}" > /dev/null
	sed -i '/\[multilib/,+1 d' /32bit/pacman.conf
	sed -i 's|Architecture = auto|Architecture = i686|g' /32bit/pacman.conf
	mkdir /run/shm || true
	mkarchroot -C /32bit/pacman.conf -M /32bit/makepkg.conf /32build/root base-devel wget #reflector
	mkdir /32build/root/pkg
	cp --copy-contents -t /32build/root/pkg /32bit/***
	cp /etc/pacman.d/antergos-mirrorlist /32build/root/etc/pacman.d

	for conf in /32bit/pacman.conf /etc/sudoers /etc/passwd /etc/group; do

		cp "${conf}" /32build/root/etc/

	done

	cp /etc/sudoers.d/10-builder /32build/root/etc/sudoers.d/
	chmod 600 /32build/root/etc/sudoers
	chmod 600 /32build/root/etc/sudoers.d/10-builder
	chmod -R 600 /usr/lib/sudo
	sed -i '1s/^/CARCH="i686"\n/' /32build/root/pkg/PKGBUILD
	find /32build/root -maxdepth 1 -exec chmod a+rw {} \;
	find /32build/root/pkg -exec chmod a+rw {} \;
	arch-chroot /32build/root pacman -Scc --noconfirm --noprogressbar --color never && pacman -Syy

	print2log 'UPDATING 32BIT SOURCE CHECKSUMS'
	check_pkg_sums 32bit
	cd /makepkg;

	{ arch-chroot /32build/root /usr/bin/bash -c "cd /pkg; sudo -u antbs /usr/bin/makepkg ${_MKPKG_OPTS}" 2>&1 && \
      cp /32build/root/pkg/*-i686.pkg.* /staging/i686 && return 0; } || return 1

}


function try_build() {

	print2log 'TRYING BUILD';
	cd /makepkg
	chmod -R a+rw /pkg
	chmod 777 /pkg
	if [[ "$1" = "i686" ]]; then

		{ build_32bit_pkg 2>&1 && rm -f /staging/i686/"${PKGNAME}"-i***.sig && return 0; } ||

		{ cd /staging/x86_64 && run_remove_pkg "${PKGNAME}" && return 1; }

	else

		cd /pkg
		print2log 'UPDATING SOURCE CHECKSUMS';
		check_pkg_sums &&
		{ sudo -u antbs makepkg "${_MKPKG_OPTS}" 2>&1 && copy_any && return 0; } || return 1

	fi

}




###=====================================================================================================
##
#      DO STUFF
##
###=====================================================================================================


print2log 'SETTING UP ENVIRONMENT'
setup_environment

if [[ "${_UPDREPO}" != "True" ]]; then

	print2log 'SYNCING REPO DATABASES'
	#pacman-key --init && pacman-key --populate archlinux antergos
	pacman -Scc --noconfirm --noprogressbar --color never 2>&1
	pacman -Syy wget --noconfirm --noprogressbar --color never 2>&1
	echo "PKGDEST=/staging/x86_64" >> /etc/makepkg.conf
	chmod -R a+rw /staging/x86_64

	repo=antergos-staging
	repo_dir=staging

	if in_array "i686" "${arch[@]}" && ! in_array "any" "${arch[@]}"; then

		print2log '[i686 DETECTED]'
		cp --copy-contents -t /32bit /pkg/***

		{ try_build 2>&1 && try_build "i686" 2>&1 && exit 0; } ||
		{ try_install_deps 2>&1 && try_build 2>&1 && try_build "i686" 2>&1 && exit 0; }

	else

		{ try_build 2>&1 && exit 0; } || { try_install_deps 2>&1 && try_build 2>&1 && exit 0; }

	fi

	# If we haven't exited before now then something went wrong. Build failed.
	exit 1;

else

	export repo="${_REPO}"
	export repo_dir="${_REPO_DIR}"
	export PKGNAME="${_PKGNAME}"
	export RESULT="${_RESULT}"
	true

fi


if [[ $? = 0 ]]; then

	if [[ ${repo_dir} = "main" ]] && [[ "${RESULT}" = "passed" ]]; then

		{ run_update_repo "${repo}" && run_remove_pkg "${PKGNAME}" && print2log "${update_success}"; } ||

		{ print2log "${update_error}" && exit 1; }

	elif [[ ${repo_dir} = "main" ]] && [[ "${RESULT}" = "failed" ]]; then

		{ run_remove_pkg "${PKGNAME}" && print2log "${update_success}"; } ||

		{ print2log "${update_error}" && exit 1; }

	elif [[ ${repo_dir} != "main" ]]; then

		{ run_update_repo "${repo}" && print2log "${update_success}"; } ||

		{ print2log "${update_error}" && exit 1; }

	else

		print2log 'BUILD FAILED' && exit 1

	fi

else

	echo print2log 'BUILD FAILED' && exit 1

fi

chown -R www-data:www-data /"${repo_dir}" && touch /result/"${PKGNAME}" && exit 0