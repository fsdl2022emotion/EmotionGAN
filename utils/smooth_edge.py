"""
Draft Function for notebook_utils.py
Fixing the rectangle edge issue on combined face.
"""

import cv2
import numpy as np
import skimage.exposure


def smooth_edge(img, set_alpha:int = 21):
    """
    Function for alpha smooth the mark image before combine

    :param img: imported image
    :param set_alpha: set transparent blur power
    :return: smoothed image
    """

    # extract alpha channel
    alpha_img = img[:, :, 3]

    # blur alpha channel
    alpha_blur = cv2.GaussianBlur(alpha_img
                                  , (0,0)
                                  , sigmaX=set_alpha
                                  , sigmaY=set_alpha)

    # stretch so that 255 -> 255 and 127.5 -> 0
    alpha_stretch = skimage.exposure.rescale_intensity(alpha_blur
                                                       , in_range=(127.5,255)
                                                       , out_range=(0,255))

    # replace alpha channel in input with new alpha channel
    smoothed_image = img.copy()
    smoothed_image[:, :, 3] = alpha_stretch

    return smoothed_image
