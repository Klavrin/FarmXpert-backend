import numpy as np

def get_area(coords):
    coords = np.array(coords)
    x = coords[:, 0]
    y = coords[:, 1]
    return np.abs(0.5 * np.array(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))))