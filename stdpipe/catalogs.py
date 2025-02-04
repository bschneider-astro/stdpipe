"""
Module containing the routines for handling various online catalogues.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os, posixpath, shutil, tempfile
import numpy as np

from astropy.table import Table, MaskedColumn, vstack
from astroquery.vizier import Vizier
from astroquery.xmatch import XMatch
from astroquery.imcce import Skybot
from astroquery.ned import Ned
from astropy.coordinates import SkyCoord
from astropy import units as u
from astropy.time import Time

from . import astrometry

catalogs = {
    'ps1': {'vizier': 'II/349/ps1', 'name': 'PanSTARRS DR1'},
    'gaiadr2': {'vizier': 'I/345/gaia2', 'name': 'Gaia DR2', 'extra': ['E(BR/RP)']},
    'gaiaedr3': {'vizier': 'I/350/gaiaedr3', 'name': 'Gaia EDR3'},
    'gaiadr3syn': {'vizier': 'I/360/syntphot', 'name': 'Gaia EDR3', 'extra': ['**', '_RAJ2000', '_DEJ2000']},
    'usnob1': {'vizier': 'I/284/out', 'name': 'USNO-B1'},
    'gsc': {'vizier': 'I/271/out', 'name': 'GSC 2.2'},
    'skymapper': {'vizier': 'II/358/smss', 'name': 'SkyMapper DR1.1'},
    'vsx': {'vizier': 'B/vsx/vsx', 'name': 'AAVSO VSX'},
    'apass': {'vizier': 'II/336/apass9', 'name': 'APASS DR9'},
    'sdss': {'vizier': 'V/147/sdss12', 'name': 'SDSS DR12', 'extra': ['_RAJ2000', '_DEJ2000']},
    'atlas': {'vizier': 'J/ApJ/867/105/refcat2', 'name': 'ATLAS-REFCAT2', 'extra': ['_RAJ2000', '_DEJ2000', 'e_Gmag', 'e_gmag', 'e_rmag', 'e_imag', 'e_zmag', 'e_Jmag', 'e_Kmag']},
}

def get_cat_vizier(ra0, dec0, sr0, catalog='ps1', limit=-1, filters={}, extra=[], verbose=False):
    """Download any catalogue from Vizier.

    The catalogue may be anything recognizable by Vizier. For some most popular ones, we have additional support - we try to augment them with photometric measurements not originally present there, based on some analytical magnitude conversion formulae. These catalogues are:

    -  ps1 - Pan-STARRS DR1. We augment it with Johnson-Cousins B, V, R and I magnitudes
    -  gaiadr2 - Gaia DR2. We augment it  with Johnson-Cousins B, V, R and I magnitudes, as well as Pan-STARRS and SDSS ones
    -  gaiaedr3 - Gaia eDR3
    -  gaiadr3syn - Gaia DR3 synthetic photometry based on XP spectra
    -  skymapper - SkyMapper DR1.1
    -  vsx - AAVSO Variable Stars Index
    -  apass - AAVSO APASS DR9
    -  sdss - SDSS DR12
    -  atlas - ATLAS-RefCat2, compilative all-sky reference catalogue with uniform zero-points in Pan-STARRS-like bands. We augment it with Johnson-Cousins B, V, R and I magnitudes the same way as Pan-STARRS.
    -  usnob1 - USNO-B1
    -  gsc - Guide Star Catalogue 2.2

    :param ra0: Right Ascension of the field center, degrees
    :param dec0: Declination of the field center, degrees
    :param sr0: Field radius, degrees
    :param catalog: Any Vizier catalogue identifier, or a catalogue short name (see above)
    :param limit: Limit for the number of returned rows, optional
    :param filters: Dictionary with column filters to be applied on Vizier side. Dictionary key is the column name, value - filter expression as documented at https://vizier.u-strasbg.fr/vizier/vizHelp/cst.htx
    :param extra: List of extra column names to return in addition to default ones.
    :param verbose: Whether to show verbose messages during the run of the function or not. May be either boolean, or a `print`-like function.
    :returns: astropy.table.Table with catalogue as returned by Vizier, with some additional columns added for supported catalogues.
    """

    # Simple Wrapper around print for logging in verbose mode only
    log = (verbose if callable(verbose) else print) if verbose else lambda *args,**kwargs: None

    # TODO: add positional errors

    if catalog in catalogs:
        # For some catalogs we may have some additional information
        vizier_id = catalogs.get(catalog).get('vizier')
        name = catalogs.get(catalog).get('name')

        columns = ['*', 'RAJ2000', 'DEJ2000', 'e_RAJ2000', 'e_DEJ2000'] + extra + catalogs.get(catalog).get('extra', [])
    else:
        vizier_id = catalog
        name = catalog
        columns = ['*', 'RAJ2000', 'DEJ2000', 'e_RAJ2000', 'e_DEJ2000'] + extra

    log('Requesting from VizieR:', vizier_id, 'columns:', columns)
    log('Center:', ra0, dec0, 'radius:', sr0)
    log('Filters:', filters)

    vizier = Vizier(row_limit=limit, columns=columns, column_filters=filters)
    cats = vizier.query_region(SkyCoord(ra0, dec0, unit='deg'), radius=sr0*u.deg, catalog=vizier_id)

    if not cats or not len(cats) == 1:
        cats = vizier.query_region(SkyCoord(ra0, dec0, unit='deg'), radius=sr0*u.deg, catalog=vizier_id, cache=False)

        if not cats or not len(cats) == 1:
            print('Error requesting catalogue', catalog)
            return None

    cat = cats[0]
    cat.meta['vizier_id'] = vizier_id
    cat.meta['name'] = name

    log('Got', len(cat), 'entries with', len(cat.colnames), 'columns')

    # Fix _RAJ2000/_DEJ2000
    if '_RAJ2000' in cat.keys() and '_DEJ2000' in cat.keys() and not 'RAJ2000' in cat.keys():
        cat.rename_columns(['_RAJ2000', '_DEJ2000'], ['RAJ2000', 'DEJ2000'])

    # Augment catalogue with additional bandpasses

    if catalog == 'ps1' or catalog == 'atlas':
        # Alternative PS1 transfromation from https://arxiv.org/pdf/1706.06147.pdf, Stetson, seems better with Landolt than official one
        # cat['B'] = cat['gmag'] + 0.199 + 0.540*(cat['gmag'] - cat['rmag']) + 0.016*(cat['gmag'] - cat['rmag'])**2
        # cat['V'] = cat['gmag'] - 0.020 - 0.498*(cat['gmag'] - cat['rmag']) - 0.008*(cat['gmag'] - cat['rmag'])**2
        # cat['R'] = cat['rmag'] - 0.163 - 0.086*(cat['gmag'] - cat['rmag']) - 0.061*(cat['gmag'] - cat['rmag'])**2
        # cat['I'] = cat['imag'] - 0.387 - 0.123*(cat['gmag'] - cat['rmag']) - 0.034*(cat['gmag'] - cat['rmag'])**2

        # My own fit on Landolt+Stetson standards from https://arxiv.org/pdf/2205.06186.pdf
        pB1,pB2 = [0.10339527794499666, -0.492149523946056, 1.2093816061394638, 0.061925048331498395], [-0.2571974580267897, 0.9211495207523038, -0.8243222108864755, 0.0619250483314976]
        pV1,pV2 = [-0.011452922062676726, -9.949308251868327e-05, -0.4650511584366353, -0.007076854914511554], [0.012749150754020416, 0.057554580469724864, -0.09019328095355343, -0.007076854914511329]
        pR1,pR2 = [0.004905242602502597, -0.046545625824660514, 0.07830702317352654, -0.08438139204305026], [-0.07782426914647306, 0.14090289318728444, -0.3634922073369279, -0.08438139204305031]
        pI1,pI2 = [-0.02274814414922734, 0.048462952908062046, -0.046965058282604985, -0.19478935830847588], [0.025124060889537177, -0.048672562735374666, -1.199591061144479, -0.1947893583084762]

        cat['B'] = cat['gmag'] + np.polyval(pB1, cat['gmag']-cat['rmag']) + np.polyval(pB2, cat['rmag']-cat['imag'])
        cat['V'] = cat['gmag'] + np.polyval(pV1, cat['gmag']-cat['rmag']) + np.polyval(pV2, cat['rmag']-cat['imag'])
        cat['R'] = cat['rmag'] + np.polyval(pR1, cat['gmag']-cat['rmag']) + np.polyval(pR2, cat['rmag']-cat['imag'])
        cat['I'] = cat['imag'] + np.polyval(pI1, cat['gmag']-cat['rmag']) + np.polyval(pI2, cat['imag']-cat['zmag'])

        cat['good'] = (cat['gmag'] - cat['rmag'] > -0.5) & (cat['gmag'] - cat['rmag'] < 2.5)
        cat['good'] &= (cat['rmag'] - cat['imag'] > -0.5) & (cat['rmag'] - cat['imag'] < 2.0)
        cat['good'] &= (cat['imag'] - cat['zmag'] > -0.5) & (cat['imag'] - cat['zmag'] < 1.0)

        # to SDSS, zero points and color terms from https://arxiv.org/pdf/1203.0297.pdf
        cat['g_SDSS'] = cat['gmag'] + 0.013 + 0.145*(cat['gmag'] - cat['rmag']) + 0.019*(cat['gmag'] - cat['rmag'])**2
        cat['r_SDSS'] = cat['rmag'] - 0.001 + 0.004*(cat['gmag'] - cat['rmag']) + 0.007*(cat['gmag'] - cat['rmag'])**2
        cat['i_SDSS'] = cat['imag'] - 0.005 + 0.011*(cat['gmag'] - cat['rmag']) + 0.010*(cat['gmag'] - cat['rmag'])**2
        cat['z_SDSS'] = cat['zmag']

    elif catalog == 'gaiadr2':
        # My simple Gaia DR2 to Johnson conversion based on Stetson standards
        pB = [-0.05927724559795761, 0.4224326324292696, 0.626219707920836, -0.011211539139725953]
        pV = [0.0017624722901609662, 0.15671377090187089, 0.03123927839356175, 0.041448557506784556]
        pR = [0.02045449129406191, 0.054005149296716175, -0.3135475489352255, 0.020545083667168156]
        pI = [0.005092289380850884, 0.07027022935721515, -0.7025553064161775, -0.02747532184796779]

        pCB = [876.4047401692277, 5.114021693079334, -2.7332873314449326, 0]
        pCV = [98.03049528983964, 20.582521666713028, 0.8690079603974803, 0]
        pCR = [347.42190542330945, 39.42482430363565, 0.8626828845232541, 0]
        pCI = [79.4028706486939, 9.176899238787003, -0.7826315256072135, 0]

        g = cat['Gmag']
        bp_rp = cat['BPmag'] - cat['RPmag']

        # phot_bp_rp_excess_factor == E(BR/RP) == E_BR_RP_
        Cstar = cat['E_BR_RP_'] - np.polyval([-0.00445024,  0.0570293,  -0.02810592,  1.20477819], bp_rp)

        # https://www.cosmos.esa.int/web/gaia/dr2-known-issues#PhotometrySystematicEffectsAndResponseCurves
        gcorr = g.copy()
        gcorr[(g>2)&(g<6)] = -0.047344 + 1.16405*g[(g>2)&(g<6)] - 0.046799*g[(g>2)&(g<6)]**2 + 0.0035015*g[(g>2)&(g<6)]**3
        gcorr[(g>6)&(g<16)] = g[(g>6)&(g<16)] - 0.0032*(g[(g>6)&(g<16)] - 6)
        gcorr[g>16] = g[g>16] - 0.032
        g = gcorr

        cat['B'] = g + np.polyval(pB, bp_rp) + np.polyval(pCB, Cstar)
        cat['V'] = g + np.polyval(pV, bp_rp) + np.polyval(pCV, Cstar)
        cat['R'] = g + np.polyval(pR, bp_rp) + np.polyval(pCR, Cstar)
        cat['I'] = g + np.polyval(pI, bp_rp) + np.polyval(pCI, Cstar)

        # to PS1 - FIXME: there are some uncorrected color and magnitude trends!
        cat['gmag'] = cat['B'] - 0.108 - 0.485*(cat['B'] - cat['V']) - 0.032*(cat['B'] - cat['V'])**2
        cat['rmag'] = cat['V'] + 0.082 - 0.462*(cat['B'] - cat['V']) + 0.041*(cat['B'] - cat['V'])**2

        # to SDSS, from https://gea.esac.esa.int/archive/documentation/GDR2/Data_processing/chap_cu5pho/sec_cu5pho_calibr/ssec_cu5pho_PhotTransf.html
        cat['g_SDSS'] = g - (0.13518 - 0.46245*bp_rp - 0.25171*bp_rp**2 + 0.021349*bp_rp**3)
        cat['r_SDSS'] = g - (-0.12879 + 0.24662*bp_rp - 0.027464*bp_rp**2 - 0.049465*bp_rp**3)
        cat['i_SDSS'] = g - (-0.29676 + 0.64728*bp_rp - 0.10141*bp_rp**2)

    elif catalog == 'skymapper':
        # to SDSS
        for _ in ['u', 'g', 'r', 'i', 'z']:
            cat[_ + '_PS1'] = cat[_ + 'PSF']

        pass

    elif catalog == 'apass':
        # My own fit based on Landolt standards
        cat['Rmag'] = cat['r_mag'] - 0.157 - 0.087*(cat['g_mag'] - cat['r_mag']) - 0.014*(cat['g_mag'] - cat['r_mag'])**2
        cat['e_Rmag'] = cat['e_r_mag']

        cat['Imag'] = cat['i_mag'] - 0.354 - 0.118*(cat['g_mag'] - cat['r_mag']) - 0.004*(cat['g_mag'] - cat['r_mag'])**2
        cat['e_Imag'] = cat['e_i_mag']

        # Copies of columns for convenience
        for _ in ['B', 'V', 'R', 'I']:
            cat[_] = cat[_ + 'mag']

    elif catalog == 'gaiadr3syn':
        # Compute magnitude errors from flux errors
        for name in ['U', 'B', 'V', 'R', 'I', 'u', 'g', 'r', 'i', 'z', 'y']:
            cat['e_' + name + 'mag'] = 2.5/np.log(10)*cat['e_F' + name] / cat['F' + name]

        # umag, gmag, rmag, imag and zmag are Sloan ugriz magnitudes! Let's get PS1 ones too
        # Fits are based on clean Landolt sample from https://arxiv.org/pdf/1203.0297.pdf
        pg = [-0.030414391501015867, -0.09960002492299584, -0.002910024005294562]
        pr = [-0.009566553708653305, 0.014924591443344211, -0.003928147919030857]
        pi = [-0.010802807724098494, 0.01124900218746879, 0.01274293783734852]
        pz = [-0.0031896767661109523, 0.06537983414287968, 0.007695587806229381]

        cat['g_ps1'] = cat['gmag'] + np.polyval(pg, cat['gmag'] - cat['rmag'])
        cat['r_ps1'] = cat['rmag'] + np.polyval(pr, cat['gmag'] - cat['rmag'])
        cat['i_ps1'] = cat['imag'] + np.polyval(pi, cat['gmag'] - cat['rmag'])
        cat['z_ps1'] = cat['zmag'] + np.polyval(pz, cat['gmag'] - cat['rmag'])

    return cat

def xmatch_objects(obj, catalog='ps1', sr=3/3600, col_ra='ra', col_dec='dec'):
    """Cross-match object list with Vizier catalogue using CDS XMatch service.

    Any Vizier catalogue may be used for cross-matching.

    :param obj: astropy.table.Table with objects
    :param catalog: Any Vizier catalogue identifier, or a catalogue short name
    :param sr: Cross-matching radius in degrees
    :param col_ra: Column name in `obj` table containing Right Ascension values
    :param col_dec: Column name in `obj` table containing Declination values
    :returns: The table of matched objects augmented with some fields from the Vizier catalogue.
    """

    if catalog in catalogs:
        vizier_id = catalogs.get(catalog)['vizier']
    else:
        vizier_id = catalog

    xcat = XMatch().query(cat1=obj, cat2='vizier:' + vizier_id, max_distance=sr*u.deg, colRA1=col_ra, colDec1=col_dec)

    return xcat

def xmatch_skybot(obj, sr=10/3600, time=None, col_ra='ra', col_dec='dec', col_id='id'):
    """Cross-match object list with positions of Solar System objects using SkyBoT service

    The routine works by requesting the list of all solar system objects in a cone containing all
    input objects, and then cross-matching them using the radius defined by `sr` parameter. Then it
    returns the table of solar system objects plus a column of unique identifiers corresponding
    to user objects.

    :param obj: astropy.table.Table with objects
    :param sr: Cross-matching radius in degrees
    :param time: Time of the observation corresponding to the objects
    :param col_ra: Column name in `obj` table containing Right Ascension values
    :param col_dec: Column name in `obj` table containing Declination values
    :param col_id: Column name in `obj` table containing some unique object identifier
    :returns: The table of solar system objects augmented with `col_id` column of matched objects.
    """

    ra0,dec0,sr0 = astrometry.get_objects_center(obj)

    try:
        # Query SkyBot for (a bit larger than) our FOV at our time
        xcat = Skybot.cone_search(SkyCoord(ra0, dec0, unit='deg'), (sr0 + 2.0*sr)*u.deg, Time(time))
    except (RuntimeError, KeyError, ConnectionError, OSError):
        # Nothing found in SkyBot
        return None

    # Cross-match objects
    oidx,cidx,dist = astrometry.spherical_match(obj[col_ra], obj[col_dec], xcat['RA'], xcat['DEC'], 10/3600)

    # Annotate the table with id from objects so that it is possible to identify the matches
    xcat[col_id] = MaskedColumn(len(xcat), dtype=np.dtype(obj[col_id][0]))
    xcat[col_id].mask = True
    xcat[col_id][cidx] = obj[col_id][oidx]
    xcat[col_id][cidx].mask = False

    return xcat

def xmatch_ned(obj, sr=3/3600, col_ra='ra', col_dec='dec', col_id='id'):
    """Cross-match object list with NED database entries

    The routine is extremely inefficient as it has to query the objects one by one!

    :param obj: astropy.table.Table with objects
    :param sr: Cross-matching radius in degrees
    :param col_ra: Column name in `obj` table containing Right Ascension values
    :param col_dec: Column name in `obj` table containing Declination values
    :param col_id: Column name in `obj` table containing some unique object identifier
    :returns: The table of NED objects augmented with `id` column containing the identifiers from `col_id` columns of matched objects.

    """

    xcat = []

    # FIXME: is there more optimal way to query NED for multiple sky positions?..
    for row in obj:
        res = Ned().query_region(SkyCoord(row[col_ra], row[col_dec], unit='deg'), sr*u.deg)

        if res:
            res['id'] = row[col_id]

            xcat.append(res)

    if xcat:
        xcat = vstack(xcat, join_type='exact', metadata_conflicts='silent')

    return xcat
