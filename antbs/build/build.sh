#!/bin/bash
# -*- coding: utf-8 -*-
#
#  build.sh
#
#  Copyright © 2014-2017 Antergos
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

DEPS=''
PKGS2_ADD_RM=( "$@" )
_filenames=()
_pkgnames=()
_generates=()



###
##
#    UTILITY FUNCTIONS
##
###


_log() {
	echo '[\^/\^/^\^/^\^/\^/\^/^\^/^\^/] ' "$1" ' [\^/\^/^\^/^\^/\^/\^/^\^/^\^/]'
}


prepare_makepkg_and_pacman_configs() {
	local _32bit
	local _PACMAN_CONF
	local _MAKEPKG_CONF

	_32bit=$1

	if [[ -z "${_32bit}" ]]; then
		_PACMAN_CONF='/etc/pacman.conf'
		_MAKEPKG_CONF='/etc/makepkg.conf'

		# Copy this before we modify it for 64bit.
		cp /etc/pacman.conf /32bit

		cp /usr/share/devtools/makepkg-x86_64.conf "${_MAKEPKG_CONF}"
		sed -i 's|unknown|x86_64|g' "${_MAKEPKG_CONF}"
		echo 'PKGDEST=/result' >> "${_MAKEPKG_CONF}"
	else
		_PACMAN_CONF='/32bit/pacman.conf'
		_MAKEPKG_CONF='/32bit/makepkg.conf'

		cp /usr/share/devtools/makepkg-i686.conf "${_MAKEPKG_CONF}"
		sed -i 's|unknown|i686|g' "${_MAKEPKG_CONF}"
	fi

	export PACKAGER="Antergos Build Server <dev@antergos.com>"
	echo "GPGKEY=24B445614FAC071891EDCE49CDBD406AA1AA7A1D" >> "${_MAKEPKG_CONF}"
	sed -i 's|#PACKAGER="John Doe <john@doe.com>"|PACKAGER="Antergos Build Server <dev@antergos.com>"|g' "${_MAKEPKG_CONF}"
	sed -i 's|CheckSpace||g;
			/\[antergos-staging/,+1 d;
			/\[antergos/,+1 d;' "${_PACMAN_CONF}"

	if [[ -z "${_32bit}" ]]; then
		sed -i '1s%^%[antergos]\nSigLevel = PackageRequired\nServer = file:///main/$arch\n%;
				1s%^%[antergos-staging]\nSigLevel = PackageRequired\nServer = file:///staging/$arch\n%;' "${_PACMAN_CONF}"
	else
		sed -i '/\[multilib/,+1 d;
				s|Architecture = auto|Architecture = i686|g;
				1s%^%[antergos]\nSigLevel = PackageRequired\nServer = http://repo.antergos.info/$repo/$arch\n%;
				1s%^%[antergos-staging]\nSigLevel = PackageRequired\nServer = http://repo.antergos.info/$repo/$arch\n%;' "${_PACMAN_CONF}"
	fi

	sed -i '' "${_PACMAN_CONF}"

}

setup_environment() {
	export HOME=/pkg

	if [[ -f /pkg/PKGBUILD ]]; then
		for file in /etc/profile.d/*.sh; do source $file; done

		source /pkg/PKGBUILD && export PKGNAME="${pkgname}"

		if [[ "${_is_metapkg}" = 'yes' ]]; then
			DEPS='-d'
			_log 'METAPKG DETECTED'
		else
			DEPS='-s'
		fi

		chmod -R a+rw /pkg
		cd /pkg

	else
		_log 'ERROR WHILE SETTING UP ENVIRONMENT (BUILD FAILED)'
		exit 1;
	fi

	prepare_makepkg_and_pacman_configs

	echo 'www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin' >> /etc/passwd
	echo 'www-data:x:33:git,www-data' >> /etc/group

	git config --global user.name "Antergos Build Server"
	git config --global user.email "admin@antergos.org"
	echo -e '[user]\n\temail = "admin@antergos.org"\n\tname = "Antergos Build Server"\n' > /.gitconfig
	cp /.gitconfig /pkg

}

fetch_upstream_pgp_keys() {
	local fake_home=/tmp/antbs/.gnupg
	[[ -n $1 ]] && fake_home="$1"

	# Mozilla Software Releases <release@mozilla.com>
	mozilla=(
		'14F26682D0916CDD81E37B6D61B7B526D98F0353'
		'F2EF4E6E6AE75B95F11F1EB51C69C4E55E9905DB'
	)

	nodejs=(
		'94AE36675C464D64BAFA68DD7434390BDBE9B9C5' 'FD3A5288F042B6850C66B31F09FE44734EB7990E'
		'71DCFD284A79C3B38668286BC97EC7A07EDE3FC1' 'DD8F2338BAE7501E3DD5AC78C273792F7D83545D'
		'C4F0DFFF4E8C1A8236409D08E73BC641CC11F4C8' 'B9AE9905FFD7803F25714661B63B535A4C206CA9'
		'56730D5401028683275BD23C23EFEFE93C4CFFFE'
	)

	install -dm777 "${fake_home}" # gpg needs $HOME to exist and be writable
	echo standard-resolver >> "${fake_home}/dirmngr.conf"
	sudo -H -u antbs gpg --keyserver hkp://pgp.mit.edu --recv-keys "${mozilla[@]}" "${nodejs[@]}"
}

in_array() {
	local e

	for e in "${@:2}"; do
		[[ "$e" = "$1" ]] && return 0;
	done

	return 1
}


create_pkg_filenames_array() {
	local pkg2_add_rm

	for pkg2_add_rm in "${PKGS2_ADD_RM[@]}"; do
		if ! in_array "${pkg2_add_rm}" "${_filenames[@]}"; then
			_filenames+=("${pkg2_add_rm}.pkg.tar.xz")
		fi
	done && export _filenames
}


create_pkgnames_array() {
	local pkg2_add_rm
	local _name

	for pkg2_add_rm in "${PKGS2_ADD_RM[@]}"; do
		if ! in_array "${pkg2_add_rm}" "${_pkgnames[@]}"; then
			_name=$(echo "${pkg2_add_rm}" | cut -d '-' -f 0)
			_pkgnames+=( "${_name}" )
		fi
	done && export _pkgnames
}


create_pkgbuild_generates_array() {
	_log 'Getting packages that would be generated by PKGBUILD...'

	{ [[ -n "$1" ]] && cd "$1" || cd /pkg; } && _generates+=( "$(sudo -u antbs makepkg --packagelist)" )

	_log "${_generates[*]}"
}


check_pkg_sums() {
	if [[ "${_AUTOSUMS}" = 'False' ]]; then
		if [[ ${1} = '' ]]; then
			sudo -u antbs /usr/bin/updpkgsums 2>&1 && return 0
		else
			arch-chroot /32build/root /usr/bin/bash -c 'cd /pkg; chmod -R a+rw /pkg; sudo -u antbs /usr/bin/updpkgsums' 2>&1 && return 0;
		fi
	else
		return 0
	fi

	return 1
}


setup_32bit_env() {
	chmod -R 777 /32build
	mkdir /run/shm || true

	prepare_makepkg_and_pacman_configs i686

	cd /32bit

	if [[ -e /32build/root ]]; then
		rm -rf /32build/root
	fi

	mkarchroot -C /32bit/pacman.conf -M /32bit/makepkg.conf -c /var/cache/pacman_i686 /32build/root base-devel wget sudo git reflector
	mkdir /32build/root/pkg
	cp --copy-contents -t /32build/root/pkg /32bit/***
	cp /etc/pacman.d/antergos-mirrorlist /32build/root/etc/pacman.d

	for conf in /32bit/{pacman,makepkg}.conf /etc/{passwd*,shadow*,group*,sudoers,resolv.conf,locale.gen,inputrc,profile.d/locale.sh}
	do
		cp "${conf}" /32build/root/etc/
	done

	cp /etc/sudoers.d/10-builder /32build/root/etc/sudoers.d/
	sed -i '1s/^/CARCH="i686"\n/' /32build/root/pkg/PKGBUILD
	chmod a+rw /32build/{root,root/pkg}
	chmod 644 /32build/root/etc/sudoers
	chmod -R 644 /32build/root/etc/sudoers.d
	chmod 755 /32build/root/etc/sudoers.d
	chmod 700 /32build/root/usr/lib/sudo
	chmod 600 /32build/root/usr/lib/sudo/*.so

	arch-chroot /32build/root reflector -l 10 -f 5 --save /etc/pacman.d/mirrorlist
	arch-chroot /32build/root pacman -Syy --noconfirm --noprogressbar --color never

	ln -s /tmp/antbs /32build/root/tmp/antbs
}


build_32bit_pkg() {
	_log 'CREATING 32-BIT BUILD ENVIRONMENT' && setup_32bit_env
	_log 'UPDATING 32BIT SOURCE CHECKSUMS' && check_pkg_sums 32bit
	cd /32bit

	{ arch-chroot \
			'/32build/root' \
			'/usr/bin/bash' \
			-c "cd /pkg; export IS_32BIT=i686; sudo -u antbs /usr/bin/makepkg -m -f -L ${DEPS} --noconfirm --needed" 2>&1 \
		&& cp /32build/root/pkg/*-i686.pkg.* /result \
		&& rm -rf /32build/root \
		&& return 0; } || rm -rf /32build/root; return 1
}


_output_pkgbuild_generates() {
	create_pkgbuild_generates_array
	echo "${_generates[*]}" >> /result/generates
}


try_build() {
	_log 'TRYING BUILD';
	chmod -R a+rw /pkg
	chmod 777 /pkg
	chown -R antbs:users /pkg

	if [[ "$1" = "i686" ]]; then

		{ build_32bit_pkg 2>&1 && return 0; } \
	||
		{ cd /result && rm **.pkg.**; return 1; }

	else
		cd /pkg && _log 'UPDATING SOURCE CHECKSUMS';

		check_pkg_sums &&
		{ sudo -u antbs makepkg -m -f -L ${DEPS} --noconfirm --needed 2>&1 \
			&& _output_pkgbuild_generates \
			&& return 0; } || { cd /result && rm **.pkg.**; return 1; }
	fi
}


pkgbuild_produces_i686_package() {
	return $(in_array 'i686' "${arch[@]}" && ! in_array 'any' "${arch[@]}")
}


build_package() {
	_log 'SYNCING REPO DATABASES'
	reflector -l 10 -f 5 --save /etc/pacman.d/mirrorlist
	pacman -Syyu --noconfirm
	chmod -R a+rw /result && chmod 777 /tmp /var /var/tmp

	export repo=antergos-staging
	export repo_dir=staging

	if [[ -d /pkg/cnchi ]]; then
		rm -rf /pkg/cnchi
	fi

	if pkgbuild_produces_i686_package; then
		_log 'i686 DETECTED'; cp --copy-contents -t /32bit /pkg/***

		{ try_build 2>&1 && try_build 'i686' 2>&1 && return 0; }
	else
		{ try_build 2>&1 && return 0; }
	fi

	return 1;
}


###
##
#    DO STUFF
##
###


_log 'SETTING UP ENVIRONMENT'
setup_environment
_log 'ADDING UPSTREAM PGP KEYS TO KEYRING'
fetch_upstream_pgp_keys 2>&1

if [[ -n "${_GET_GENERATES}" ]]; then
	echo "${_generates[@]}" >> /result/generates
	exit 0
fi

build_package || { _log 'BUILD FAILED' && exit 1; }

#while [[ -z "${ANTBS_STOP}" ]]
#do
#	sleep 15
#done

