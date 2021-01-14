import geopandas as gpd
from shapely import geometry as sg
import requests
import os
from requests.auth import HTTPBasicAuth
import time
from pandas import json_normalize
from pathlib import Path
import geemap
import ipyvuetify as v
import json
from zipfile import ZipFile

def paginate(session, url):
    page_url = url
    page = session.get(page_url).json()
    
    return page

def get_scenes_per_quad(quad, session):
    url = quad["_links"]["items"]
    result = session.get(url)
    scenes = result.json()

    return scenes["items"]

def get_quads(url, payload, session):
    
    result = session.get(url, params=payload)
    page = result.json()

    if result.status_code < 299:
        quads = page["items"]
        while "_next" in page["_links"]:
            page = paginate(session, page["_links"].get("_next"))
            quads += page["items"]
    else:
        quads = result

    return quads

def download_quads(quads, mosaic, session, output):
    
    mosaic_path = os.path.join(os.path.expanduser('~'), 'downloads', mosaic)
    if not os.path.exists(mosaic_path):
        os.mkdir(mosaic_path)

    for quad in quads:
        location = quad["_links"]["download"]
        quad_id = quad["id"]
        quad_file = os.path.join(mosaic_path, f"{quad_id}.tif")

        if not os.path.isfile(quad_file):
            worked = False
            while not worked:
                try:
                    download = session.get(location)
                    worked = True

                    f = open(quad_file, "wb")
                    output.add_msg(f'Downloading mosaic quad {quad_file}')
                    for chunk in download.iter_content(chunk_size=512 * 1024):
                        # filter out keep-alive new chunks
                        if chunk:
                            f.write(chunk)
                    f.close()
                    output.add_msg(f'{mosaic} quad ID {quad_id} saved.')
                except:
                    output.add_msg('Connection refused by the server..', 'warning')
                    time.sleep(5)
                    output.add_msg('Was a nice sleep, now let me continue...', 'warning')
                    continue
        else:
            output.add_msg(f'{mosaic} quad ID {quad_id} exists.')
    
    output.add_msg(f"The result are now available in the following folder : {mosaic_path}/", "success")
    
    return mosaic_path

def get_error(code, **kwargs):

    error_codes = {
        "e1": "There seem to be an error with your API access, please check your API key.",
        "e2": "Error finding your mosaics, try checking that you used the correct mosaic name.",
        "e3": "There was an error with your geometry, please check the GeoJSON file.",
        "e4": f"Error {kwargs['quads'].status_code}: {kwargs['quads'].reason}, {kwargs['quads'].text.split('<p>')[1][:-5]} \nProcess terminated"
    }

    return error_codes[code]

def get_orders(planet_api_key, basemaps_url, output):
    
    # authenticate to planet
    command = [
        'curl', '-L', '-H',
        f"Authorization: api-key {planet_api_key}",
        basemaps_url
    ]
    os.system(' '.join(command))
    
    session = requests.Session()
    session.auth = HTTPBasicAuth(planet_api_key, '')
    response = session.get(basemaps_url, params={'_page_size': 1000})

    output.add_msg(str(response))
    
    # Getting mosaics metadata
    orders = response.json()["mosaics"]
    if len(orders) == 0:
        output.add_msg(get_error("e1"), 'error')
        return
        
    
    output.add_live_msg('Orders list updated', 'success')
    
    # construct a order dict with only the name and the index
    return {order['name']: i for i, order in enumerate(orders)}

def get_grid(planet_api_key, basemaps_url, aoi_io, m, output):
    
    #authenticate to planet
    command = [
        'curl', '-L', '-H',
        f"Authorization: api-key {planet_api_key}",
        basemaps_url
    ]
    os.system(' '.join(command))
    
    session = requests.Session()
    session.auth = HTTPBasicAuth(planet_api_key, '')
    response = session.get(basemaps_url, params={'_page_size': 1000})

    output.add_msg(str(response))
    
    # Getting mosaics metadata
    orders = response.json()["mosaics"]
    if len(orders) == 0:
        output.add_msg(get_error("e1"), 'error')
        return
    
    # select the first order
    mosaic_df = json_normalize(orders)
    mosaic_url = basemaps_url + mosaic_df.iloc[0].id

    # Getting the quads
    quads_url = mosaic_url + '/quads'
    payload = {"_page_size": 1000, 'bbox': ', '.join(str(x) for x in aoi_io.get_bounds(aoi_io.get_aoi_ee()))}
    quads = get_quads(quads_url, payload, session)
    
    df = json_normalize(quads)
    geometry = [sg.box(*row.bbox) for i, row in df.iterrows()]
    gdf = gpd.GeoDataFrame(df.filter(['id']), geometry=geometry, crs="EPSG:4326")
    
    grid_path = Path('~', 'downloads', f'{aoi_io.get_aoi_name()}_planet_grid.shp').expanduser()
    gdf.to_file(grid_path)
    
    # display on map 
    json_df = json.loads(gdf.to_json())
    ee_df = geemap.geojson_to_ee(json_df)
    
    m.addLayer(ee_df, {'color': v.theme.themes.dark.accent}, 'grid')
    m.zoom_ee_object(ee_df.geometry())
    
    return grid_path
    
def run_download(planet_api_key, basemaps_url, aoi_io, order_index, output):
    
    mosaic_path = None
    
    #authenticate to planet
    command = [
        'curl', '-L', '-H',
        f"Authorization: api-key {planet_api_key}",
        basemaps_url
    ]
    os.system(' '.join(command))
    
    session = requests.Session()
    session.auth = HTTPBasicAuth(planet_api_key, '')
    response = session.get(basemaps_url, params={'_page_size': 1000})

    output.add_msg(str(response))
    
    # Getting mosaics metadata
    orders = response.json()["mosaics"]
    if len(orders) == 0:
        output.add_msg(get_error("e1"), 'error')
        return
    
    # maybe insert number as a variable in the interface    
    mosaic_name = orders[order_index]["name"]
    
    mosaics_df = json_normalize(orders)

    if mosaic_name in mosaics_df.name.values:
        mosaic_id = mosaics_df.loc[mosaics_df["name"] == mosaic_name]["id"].values
        mosaic_url = basemaps_url + mosaic_id[0]

        # Getting the quads
        quads_url = mosaic_url + '/quads'
        payload = {"_page_size": 1000, 'bbox': ', '.join(str(x) for x in aoi_io.get_bounds(aoi_io.get_aoi_ee()))}

        quads = get_quads(quads_url, payload, session)

        #if isinstance(quads, list):
        #    output.add_msg(f"Preparing the download of {len(quads)} quads for mosaic {mosaic_name}")
        #    mosaic_path = download_quads(quads, mosaic_name, session, output)
        #    
        #else:
        #    output.add_msg(get_error("e4", quads=quads), 'error')
    
    mosaic_path = os.path.join(os.path.expanduser('~'), 'downloads', mosaic_name)
    
    return create_zip(mosaic_path)
            
def create_zip(mosaic_path):
    """Create a zipfile from the images in the repository"""
    
    # convert to pathlib path
    mosaic_path = Path(mosaic_path)
    
    # create the zipfile 
    zip_file = mosaic_path.joinpath(f'{mosaic_path.name}.zip')
    with ZipFile(zip_file, 'w') as myzip:
        
        for file in mosaic_path.glob('*.tif'):
            myzip.write(file)
            
    return zip_file
    
def get_sum_up(aoi_io):
    
    min_lon, min_lat, max_lon, max_lat = aoi_io.get_bounds(aoi_io.get_aoi_ee())
    sg_bb = sg.box(min_lon, min_lat, max_lon, max_lat)
    
    gdf = gpd.GeoDataFrame(crs="EPSG:4326", geometry = [sg_bb]).to_crs('ESRI:54009')
    minx, miny, maxx, maxy = gdf.total_bounds
    surface = (maxx-minx)*(maxy-miny)/10e6 #in km2
    
    msg = f"You're about to launch a downloading on a surface of {surface} Km\u00B2"
    
    return msg