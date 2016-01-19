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
#  the Free Software Foundation; either version 3 of the License, or
#  (at your option) any later version.
#
#  AntBS is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  The following additional terms are in effect as per Section 7 of the license:
#
#  The preservation of all legal notices and author attributions in
#  the material or in the Appropriate Legal Notices displayed
#  by works containing it is required.
#
#  You should have received a copy of the GNU General Public License
#  along with AntBS; If not, see <http://www.gnu.org/licenses/>.
pkg_deps="$*"
DEPS=''



###=====================================================================================================
##
#      HELPER FUNCTIONS
##
###=====================================================================================================


function print2log() {

	echo '[\^/\^/^\^/^\^/\^/\^/^\^/^\^/] ' "${1}" ' [\^/\^/^\^/^\^/\^/\^/^\^/^\^/]'

}


function setup_environment() {

	update_error='ERROR UPDATING STAGING REPO (BUILD FAILED)'
	update_success='STAGING REPO UPDATE COMPLETE'
	export HOME=/root

	if [[ -f /pkg/PKGBUILD ]]; then

		source /pkg/PKGBUILD && export PKGNAME="${pkgname}"

		if [[ "${_is_metapkg}" = 'yes' ]]; then
			DEPS='-d'
			print2log 'METAPKG DETECTED...'
		else
			DEPS='-s'
		fi

		chmod -R a+rw /pkg
		cd /pkg

	elif [[ "True" != "${_UPDREPO}" ]]; then

		print2log 'ERROR WHILE SETTING UP ENVIRONMENT (BUILD FAILED)'
		exit 1;

	fi

	if [[ ${_GET_PKGVER_ONLY} = "True" ]]; then
		if [[ $(type pkgver) = "function" ]]; then
			touch "/result/$(pkgver)" && exit 0
		else
			touch "/result/${pkgver}" && exit 0
		fi
		exit 1
	fi

	if [[ "${_ALEXPKG}" = "False" ]]; then

		echo "GPGKEY=24B445614FAC071891EDCE49CDBD406AA1AA7A1D" >> /etc/makepkg.conf
		export PACKAGER="Antergos Build Server <dev@antergos.com>"
		sed -i 's|#PACKAGER="John Doe <john@doe.com>"|PACKAGER="Antergos Build Server <dev@antergos.com>"|g' /etc/makepkg.conf
		sed -i '/\[antergos-staging/,+1 d' /etc/pacman.conf
		sed -i '1s%^%[antergos-staging]\nSigLevel = Never\nServer = file:///staging/$arch\n%' /etc/pacman.conf
		sed -i 's|Include = /etc/pacman.d/antergos-mirrorlist|Server = file:///$repo/$arch\n|g' /etc/pacman.conf

	else

		export PACKAGER="Alexandre Filgueira <alexfilgueira@cinnarch.com>"
		sed -i 's|#PACKAGER="John Doe <john@doe.com>"|PACKAGER="Alexandre Filgueira <alexfilgueira@cinnarch.com>"|g' /etc/makepkg.conf
		sed -i '/\[antergos/,+1 d' /etc/pacman.conf
		sed -i '/\[antergos-staging/,+1 d' /etc/pacman.conf
		sed -i '1s%^%[antergos-staging]\nSigLevel = Never\nServer = file:///staging/$arch\n%' /etc/pacman.conf
		#sed -i '/\[antergos-staging/,+1 d' /etc/pacman.conf

	fi

	sed -i 's|CheckSpace||g' /etc/pacman.conf
	sed -i '/CFLAGS=/c\CFLAGS="-march=native -mtune=generic -O2 -pipe -fstack-protector-strong --param=ssp-buffer-size=4"' /etc/makepkg.conf
	sed -i '/CXXFLAGS=/c\CXXFLAGS="-march=native -mtune=generic -O2 -pipe -fstack-protector-strong --param=ssp-buffer-size=4"' /etc/makepkg.conf
	sed -i '/#MAKEFLAGS=/c\MAKEFLAGS="-j6"' /etc/makepkg.conf
	echo 'BUILDDIR=/var/tmp' >> /etc/makepkg.conf
	echo "www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin" >> /etc/passwd
	echo "www-data:x:33:git,faidoc,karasu,phabd,www-data" >> /etc/group

	git config --global user.name "Antergos Build Server"
	git config --global user.email "admin@antergos.org"
	echo -e '[user]\n\temail = "admin@antergos.org"\n\tname = "Antergos Build Server"\n' > /.gitconfig

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
		repo-add -R -f "${repo}.db.tar.gz" ./${PKGNAME}**.xz
	done && touch "/result/${PKGNAME}" && return 0;

	return 1

}


function run_remove_pkg() {

	local repo_dir=staging
	local repo=antergos-staging
	print2log "REMOVING ${1} FROM STAGING REPO";

	for arc in i686 x86_64; do
		cd "/${repo_dir}/${arc}"
		repo-remove "${repo}.db.tar.gz" "${PKGNAME}"
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
	done && return 0;

	return 1;

}

function check_pkg_sums() {

	if [[ ${_AUTOSUMS} = "False" ]]; then
		if [[ ${1} = '' ]]; then
			sudo -u antbs /usr/bin/updpkgsums 2>&1 && return 0
		else
			arch-chroot /32build/root /usr/bin/bash -c "cd /pkg; chmod -R a+rw /pkg; sudo -u antbs /usr/bin/updpkgsums" 2>&1 && return 0;
		fi
	else
		return 0
	fi

	return 1

}

function setup_32bit_env() {

	chmod -R 777 /32build
	chmod -R a+rw /staging/i686
	cp /usr/share/devtools/makepkg-i686.conf /32bit/makepkg.conf
	sed -i '/#MAKEFLAGS=/c\MAKEFLAGS="-j6"' /32bit/makepkg.conf
	echo 'BUILDDIR=/var/tmp' >> /32bit/makepkg.conf
	cp /etc/pacman.conf /32bit
	sed -i '/\[multilib/,+1 d' /32bit/pacman.conf
	sed -i 's|Architecture = auto|Architecture = i686|g' /32bit/pacman.conf
	mkdir /run/shm || true

	if [[ ${_ALEXPKG} = False ]]; then

		echo "GPGKEY=24B445614FAC071891EDCE49CDBD406AA1AA7A1D" >> /32bit/makepkg.conf
		sed -i 's|#PACKAGER="John Doe <john@doe.com>"|PACKAGER="Antergos Build Server <dev@antergos.com>"|g' /32bit/makepkg.conf
		cd /32bit
	else
		sed -i '/\[antergos/,+1 d' /32bit/pacman.conf
		sed -i 's|#PACKAGER="John Doe <john@doe.com>"|PACKAGER="Alexandre Filgueira <alexfilgueira@cinnarch.com>"|g' /32bit/makepkg.conf
		sed -i '/\[antergos-staging/,+1 d' /32bit/pacman.conf
		sed -i '1s%^%[antergos-staging]\nSigLevel = Never\nServer = file:///staging/$arch\n%' /32bit/pacman.conf
	fi

	if [[ -e /32build/root ]]; then
		rm -rf /32build/root
	fi

	mkarchroot -C /32bit/pacman.conf -M /32bit/makepkg.conf -c /var/cache/pacman_i686/pkg /32build/root base-devel wget sudo git reflector
	mkdir /32build/root/pkg
	cp --copy-contents -t /32build/root/pkg /32bit/***
	cp /etc/pacman.d/antergos-mirrorlist /32build/root/etc/pacman.d

	for conf in /32bit/pacman.conf /32bit/makepkg.conf /etc/sudoers /etc/passwd /etc/group; do

		cp "${conf}" /32build/root/etc/

	done

	cp /etc/sudoers.d/10-builder /32build/root/etc/sudoers.d/
	sed -i '1s/^/CARCH="i686"\n/' /32build/root/pkg/PKGBUILD
	chmod a+rw /32build/root
	chmod a+rw /32build/root/pkg
	chmod 644 /32build/root/etc/sudoers
	chmod -R 644 /32build/root/etc/sudoers.d
	chmod 755 /32build/root/etc/sudoers.d
	chmod 700 /32build/root/usr/lib/sudo
	chmod 600 /32build/root/usr/lib/sudo/*.so
	sed -i 's|file:\/\/\/\$repo/\$arch|http://repo.antergos.info/\$repo/\$arch|g' /32build/root/etc/pacman.conf
	sed -i 's|file:\/\/\/staging/\$arch|http://repo.antergos.info/\$repo/\$arch|g' /32build/root/etc/pacman.conf
	mount -o bind /var/cache/pacman_i686 /32build/root/var/cache/pacman
	arch-chroot /32build/root pacman -Syy --noconfirm --noprogressbar --color never
	arch-chroot /32build/root reflector -l 10 -f 5 --save /etc/pacman.d/mirrorlist

}


function build_32bit_pkg() {

	print2log 'CREATING 32-BIT BUILD ENVIRONMENT';
	setup_32bit_env
	print2log 'UPDATING 32BIT SOURCE CHECKSUMS'
	check_pkg_sums 32bit
	cd /32bit

	{ arch-chroot /32build/root /usr/bin/bash -c "cd /pkg; export IS_32BIT=i686; sudo -u antbs /usr/bin/makepkg -m -f -L ${DEPS} --noconfirm --noprogressbar --needed" 2>&1 && \
      cp /32build/root/pkg/*-i686.pkg.* /staging/i686 && return 0; } || return 1

}


function try_build() {

	print2log 'TRYING BUILD';
	chmod -R a+rw /pkg
	chmod 777 /pkg
	if [[ "$1" = "i686" ]]; then

		{ build_32bit_pkg 2>&1 && rm -f /staging/i686/"${PKGNAME}"-i***.sig && return 0; } ||

		{ cd /staging/x86_64 && run_remove_pkg "${PKGNAME}" && return 1; }

	else

		cd /pkg
		print2log 'UPDATING SOURCE CHECKSUMS';
		check_pkg_sums &&
		{ sudo -u antbs makepkg -m -f -L ${DEPS} --noconfirm --noprogressbar --needed 2>&1 && copy_any && return 0; } || return 1

	fi

}




###=====================================================================================================
##
#      DO STUFF
##
###=====================================================================================================


print2log 'SETTING UP ENVIRONMENT'
setup_environment

if [[ "True" != "${_UPDREPO}" ]]; then

	print2log 'SYNCING REPO DATABASES'
	reflector -l 10 -f 5 --save /etc/pacman.d/mirrorlist
	pacman -Syyu --noconfirm
	echo "PKGDEST=/staging/x86_64" >> /etc/makepkg.conf
	chmod -R a+rw /staging/x86_64

	repo=antergos-staging
	repo_dir=staging

	if in_array "i686" "${arch[@]}" && ! in_array "any" "${arch[@]}"; then

		print2log '[i686 DETECTED]'
		cp --copy-contents -t /32bit /pkg/***

		{ try_build 2>&1 && try_build "i686" 2>&1 && touch /result && exit 0; } || exit 1
		# { try_install_deps 2>&1 && try_build 2>&1 && try_build "i686" 2>&1 && touch /result && exit 0; }

	else

		{ try_build 2>&1 && touch /result && exit 0; } || exit 1 #{ try_install_deps 2>&1 && try_build 2>&1 && touch /result && exit 0; }

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

		{ run_update_repo "${repo}" && run_remove_pkg && print2log "${update_success}"; } ||

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

	print2log 'BUILD FAILED' && exit 1

fi

chown -R 33:33 /"${repo_dir}" && touch /result/"${PKGNAME}" && exit 0
