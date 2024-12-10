# Licensed under a 3-clause BSD style license - see LICENSE.rst

import numpy as np
from scipy.stats import chi2, ncx2


def sigma_to_ts(n_sigma, df=1, method="wilk"):
    """Convert number of sigma to delta ts.

    The theorem is valid only if:
    - the two hypotheses tested can be defined in the same parameters space
    - the true value is not at the boundary of this parameters space.

    Parameters
    ----------
    n_sigma : float
        Significance in number of sigma.
    df : int
        Number of degree of freedom.
    method : str
        Method used to compute the statistics:
            * wilk : applies Wilk's theorem [1]
                The theorem is valid only if :
                - the two hypotheses tested can be defined in the same parameters space
                - the true value is not at the boundary of this parameters space

            * cowan : uses the statistic described in Cowan et al. (2011) [2]
        Default is `wilk`.

    Returns
    -------
    ts : float
        Test statistic

    References
    ----------
    .. [1] Wilks theorem: https://en.wikipedia.org/wiki/Wilks%27_theorem

    .. [2] Cowan et al. (2011), European Physical Journal C, 71, 1554.
        doi:10.1140/epjc/s10052-011-1554-0.
    """
    if method == "wilk":
        p_value = chi2.sf(n_sigma**2, df=1)
        return chi2.isf(p_value, df=df)
    elif method == "cowan":
        ts = n_sigma**2
        p_value = ncx2.sf(ts, df=1, nc=ts)
        return ncx2.isf(p_value, df=df, nc=ts)
    else:
        raise ValueError(
            f"Invalid method : {method}, posible options are 'wilk' or 'cowan'"
        )


def ts_to_sigma(ts, df=1, method="wilk"):
    """Convert delta ts to number of sigma.

    Parameters
    ----------
    ts : float
        Test statistic.
    df : int
        Number of degree of freedom.
    method : str
        Method used to compute the statistics:
            * wilk : applies Wilk's theorem [1]
                The theorem is valid only if :
                - the two hypotheses tested can be defined in the same parameters space
                - the true value is not at the boundary of this parameters space

            * cowan : uses the statistic described in Cowan et al. (2011) [2]
        Default is `wilk`.

    Returns
    -------
    n_sigma : float
        Significance in number of sigma.

    References
    ----------
    .. [1] Wilks theorem: https://en.wikipedia.org/wiki/Wilks%27_theorem

    .. [2] Cowan et al. (2011), European Physical Journal C, 71, 1554.
        doi:10.1140/epjc/s10052-011-1554-0.

    """
    if method == "wilk":
        p_value = chi2.sf(ts, df=df)
        return np.sqrt(chi2.isf(p_value, df=1))
    elif method == "cowan":
        p_value = ncx2.sf(ts, df=df, nc=ts)
        return np.sqrt(ncx2.isf(p_value, df=1, nc=ts))
    else:
        raise ValueError(
            f"Invalid method : {method}, posible options are 'wilk' or 'cowan'"
        )
