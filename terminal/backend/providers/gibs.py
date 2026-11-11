"""NASA GIBS satellite imagery tiles (SAT module + map base layers). No key.
Returns an XYZ tile-URL template for web maps (MapLibre/Leaflet).
"""
def tile_template(layer="MODIS_Terra_CorrectedReflectance_TrueColor",
                  date="default", matrix="GoogleMapsCompatible_Level9", ext="jpg"):
    return (f"https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{layer}/default/"
            f"{date}/{matrix}/{{z}}/{{y}}/{{x}}.{ext}")

LAYERS = {
    "truecolor": "MODIS_Terra_CorrectedReflectance_TrueColor",
    "viirs_truecolor": "VIIRS_SNPP_CorrectedReflectance_TrueColor",
    "night_lights": "VIIRS_Black_Marble",
    "sea_surface_temp": "GHRSST_L4_MUR_Sea_Surface_Temperature",
}
