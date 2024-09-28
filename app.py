import streamlit as st
import json
import requests
from shapely.geometry import shape, MultiPolygon, Polygon, box
from shapely.ops import unary_union
from shapely.validation import make_valid
import folium
from streamlit_folium import st_folium
import geopandas as gpd
import osmnx as ox
import asyncio
import aiohttp
import pandas as pd

# Set page config
st.set_page_config(page_title="Davis's Fun Map Generator!", page_icon="🌍", layout="wide")

# Define color scheme
PRIMARY_COLOR = "#BB86FC"
SECONDARY_COLOR = "#03DAC6"
BACKGROUND_COLOR = "#121212"
TEXT_COLOR = "#E0E0E0"

# Custom CSS for dark mode, Roboto font, and flat design
st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;700&display=swap');
    
    body {{
        color: {TEXT_COLOR};
        background-color: {BACKGROUND_COLOR};
        font-family: 'Roboto', sans-serif;
    }}
    
    .stButton > button {{
        width: 100%;
        background-color: {PRIMARY_COLOR};
        color: {BACKGROUND_COLOR};
        padding: 10px;
        font-size: 16px;
        border: none;
        border-radius: 4px;
        cursor: pointer;
        transition: all 0.3s ease;
    }}
    .stButton > button:hover {{
        background-color: {SECONDARY_COLOR};
        color: {BACKGROUND_COLOR};
    }}
    .stDownloadButton > button {{
        background-color: {SECONDARY_COLOR};
        color: {BACKGROUND_COLOR};
    }}
    .stDownloadButton > button:hover {{
        background-color: {PRIMARY_COLOR};
        color: {BACKGROUND_COLOR};
    }}
    .stTextInput > div > div > input {{
        background-color: {BACKGROUND_COLOR};
        color: {TEXT_COLOR};
        border: 1px solid {PRIMARY_COLOR};
    }}
    .stSelectbox > div > div > select {{
        background-color: {BACKGROUND_COLOR};
        color: {TEXT_COLOR};
        border: 1px solid {PRIMARY_COLOR};
    }}
    .stAlert {{
        background-color: {BACKGROUND_COLOR};
        color: {TEXT_COLOR};
        border: 1px solid {PRIMARY_COLOR};
        padding: 10px;
        border-radius: 4px;
        animation: fadeIn 0.5s;
    }}
    @keyframes fadeIn {{
        0% {{ opacity: 0; }}
        100% {{ opacity: 1; }}
    }}
    .stTab {{
        background-color: {BACKGROUND_COLOR};
        color: {TEXT_COLOR};
    }}
    .stTab > div {{
        background-color: {BACKGROUND_COLOR};
        color: {TEXT_COLOR};
    }}
    .stDataFrame {{
        background-color: {BACKGROUND_COLOR};
        color: {TEXT_COLOR};
    }}
    </style>
    """, unsafe_allow_html=True)

def make_request(url, params=None, headers=None, method='GET', data=None):
    try:
        response = requests.request(method, url, params=params, headers=headers, data=data)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        st.error(f"API request failed: {str(e)}")
        return None

def validate_location(location, location_type):
    params = {'q': location, 'format': 'json', 'limit': 1, 'featuretype': location_type}
    headers = {'User-Agent': 'GeojsonGenerator/1.0'}
    response = make_request("https://nominatim.openstreetmap.org/search", params, headers)
    
    if response:
        return response[0]
    return None

def build_overpass_query(location):
    return f"[out:json];{location['osm_type']}({location['osm_id']});(._;>;);out geom;"

def build_streets_overpass_query(location):
    area_id = int(location['osm_id']) + 3600000000 if location['osm_type'] == 'relation' else int(location['osm_id'])
    return f"[out:json];area({area_id})->.searchArea;(way['highway']['highway'!~'path|footway|cycleway|bridleway|steps|platform|construction']['area'!~'yes'](area.searchArea););out geom;"

def create_geometry(element, ways, streets_only):
    if element['type'] == 'way':
        coords = [[node['lon'], node['lat']] for node in element['geometry']]
        if len(coords) < 2:
            return None  # Not enough coordinates to form a valid geometry
        if len(coords) >= 4 and coords[0] == coords[-1]:
            return {"type": "Polygon", "coordinates": [coords]}
        else:
            return {"type": "LineString", "coordinates": coords}
    elif not streets_only and element['type'] == 'relation':
        outer_ways = []
        for m in element.get('members', []):
            if m['type'] == 'way' and m['role'] == 'outer':
                way_coords = [[node['lon'], node['lat']] for node in ways.get(m['ref'], {}).get('geometry', [])]
                if len(way_coords) >= 4:
                    try:
                        outer_ways.append(Polygon(way_coords))
                    except ValueError:
                        # Skip invalid polygons
                        pass
        if outer_ways:
            try:
                multi_poly = unary_union(outer_ways)
                # Attempt to make the geometry valid
                multi_poly = make_valid(multi_poly)
                if isinstance(multi_poly, Polygon):
                    return {"type": "Polygon", "coordinates": [list(multi_poly.exterior.coords)]}
                elif isinstance(multi_poly, MultiPolygon):
                    return {"type": "MultiPolygon", "coordinates": [[list(poly.exterior.coords)] for poly in multi_poly.geoms]}
            except Exception as e:
                st.warning(f"Error creating geometry for relation {element['id']}: {str(e)}")
    return None

def osm_to_geojson(osm_data, streets_only=False):
    features = []
    ways = {e['id']: e for e in osm_data['elements'] if e['type'] == 'way'}

    for element in osm_data['elements']:
        if streets_only and element['type'] != 'way':
            continue

        geometry = create_geometry(element, ways, streets_only)
        if geometry:
            features.append({
                "type": "Feature",
                "properties": {
                    "name": element.get('tags', {}).get('name', 'Unknown'),
                    "osm_id": element['id'],
                    "osm_type": element['type'],
                    "geometry": geometry
                },
                "geometry": geometry
            })

    return {"type": "FeatureCollection", "features": features}

def validate_geojson(geojson_data):
    try:
        for feature in geojson_data['features']:
            shape(feature['geometry'])
        return True
    except Exception as e:
        st.error(f"Validation error: {str(e)}")
        return False

async def fetch_osm_data(session, url, data):
    async with session.post(url, data=data) as response:
        return await response.json()

async def fetch_osm_data_osmnx(session, polygon):
    # Fetch street data for a given polygon using osmnx and aiohttp
    try:
        gdf = ox.features_from_polygon(polygon, tags={'highway': True})
        return gdf
    except Exception as e:
        st.error(f"Error retrieving street data using osmnx: {str(e)}")
        return None

async def generate_geojson_concurrent(location, query_builder, streets_only=False):
    if streets_only:
        # Use osmnx with concurrency for faster street network retrieval
        try:
            if location['osm_type'] == 'relation':
                # Get the bounding box of the area
                bbox = ox.geocode_to_gdf(location['display_name']).total_bounds

                # Divide the bounding box into smaller boxes (adjust num_rows/num_cols as needed)
                num_rows = 50  
                num_cols = 50 
                width = (bbox[2] - bbox[0]) / num_cols
                height = (bbox[3] - bbox[1]) / num_rows

                # Create a list of polygons representing the smaller boxes
                polygons = []
                for i in range(num_rows):
                    for j in range(num_cols):
                        minx = bbox[0] + j * width
                        miny = bbox[1] + i * height
                        maxx = minx + width
                        maxy = miny + height
                        polygons.append(box(minx, miny, maxx, maxy))

                async with aiohttp.ClientSession() as session:
                    tasks = []
                    for polygon in polygons:
                        tasks.append(fetch_osm_data_osmnx(session, polygon))
                    gdfs = await asyncio.gather(*tasks)

                # Combine the GeoDataFrames from all boxes
                gdf = gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True))
            else:
                # Use ox.graph_from_place for smaller areas (cities, towns, etc.)
                gdf = ox.graph_from_place(location['display_name'], network_type='drive', which_result=1)
                gdf = ox.graph_to_gdfs(gdf, nodes=False, edges=True)

            geojson_data = json.loads(gdf.to_json())
            return geojson_data
        except Exception as e:
            st.error(f"Error retrieving street data using osmnx: {str(e)}")
            return None
    else:
        # Use asyncio and aiohttp for concurrent Overpass API requests
        query = query_builder(location)

        # Split the query into smaller chunks (you might need to adjust the chunk size)
        chunk_size = 500
        queries = [query[i:i + chunk_size] for i in range(0, len(query), chunk_size)]

        async with aiohttp.ClientSession() as session:
            tasks = []
            for q in queries:
                tasks.append(fetch_osm_data(session, "http://overpass-api.de/api/interpreter", data=q))
            responses = await asyncio.gather(*tasks)

        # Combine the results from all chunks
        combined_data = {'elements': []}
        for r in responses:
            combined_data['elements'].extend(r.get('elements', []))

        geojson_data = osm_to_geojson(combined_data, streets_only=streets_only)

        if validate_geojson(geojson_data):
            return geojson_data
        else:
            st.error('Generated GeoJSON is not valid.')
            return None

def generate_geojson_wrapper(location, query_builder, streets_only=False):
    # Wrapper function to run the async function in a synchronous context
    return asyncio.run(generate_geojson_concurrent(location, query_builder, streets_only))


def display_map(geojson_data):
    # Create a GeoDataFrame from the GeoJSON features
    gdf = gpd.GeoDataFrame.from_features(geojson_data['features'])

    # Ensure the geometry column is set
    if 'geometry' not in gdf.columns:
        st.error("No geometry column found in the GeoDataFrame.")
        return

    # Convert the geometry column to Shapely geometry objects
    gdf['geometry'] = gdf['geometry'].apply(shape)

    gdf = gdf.set_geometry('geometry')

    # Calculate bounds and center
    bounds = gdf.total_bounds
    center = [(bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2]

    # Create a folium map centered on the calculated center
    m = folium.Map(location=center, zoom_start=10, tiles="cartodbdark_matter")

    # Add GeoJSON to the map with style function
    def style_function(_):
        return {
            'fillColor': PRIMARY_COLOR,
            'color': SECONDARY_COLOR,
            'weight': 2,
            'fillOpacity': 0.7,
        }

    st_folium(m, geojson_data, style_function=style_function, name="geojson")

    # Fit the map to the bounds of the GeoJSON
    m.fit_bounds([(bounds[1], bounds[0]), (bounds[3], bounds[2])])

    # Add layer control
    folium.LayerControl().add_to(m)

    # Display the map
    st_folium(m)


def display_data_preview(geojson_data):
    gdf = gpd.GeoDataFrame.from_features(geojson_data['features'])
    st.dataframe(gdf.drop(columns=['geometry']).head())


def calculate_area(gdf):
    if gdf.crs is None:
        # If CRS is not set, assume WGS84 (EPSG:4326)
        gdf = gdf.set_crs(epsg=4326, inplace=False)

    # Use an equal-area projection for accurate area calculation
    gdf_area = gdf.to_crs('+proj=cea')
    return gdf_area.area.sum() / 1e6  # Convert to square kilometers


def calculate_total_street_length(gdf):
    if gdf.crs is None:
        # If CRS is not set, assume WGS84 (EPSG:4326)
        gdf = gdf.set_crs(epsg=4326, inplace=False)

    # Use an equal-area projection for accurate length calculation
    gdf_length = gdf.to_crs('+proj=cea')
    return gdf_length.length.sum() / 1000  # Convert to kilometers


def display_statistics(geojson_data):
    gdf = gpd.GeoDataFrame.from_features(geojson_data['features'])

    # Ensure the geometry column is set
    if 'geometry' not in gdf.columns:
        st.error("No geometry column found in the GeoDataFrame.")
        return

    # Convert the geometry column to Shapely geometry objects
    gdf['geometry'] = gdf['geometry'].apply(shape)

    gdf = gdf.set_geometry('geometry')

    st.write("📊 General Statistics:")
    st.write(f"Total features: {len(gdf)}")

    geometry_types = gdf.geometry.type.value_counts().to_dict()
    st.write("Geometry types:")
    for geo_type, count in geometry_types.items():
        st.write(f"- {geo_type}: {count}")

    try:
        area = calculate_area(gdf)
        st.write(f"📐 Total area: {area:.2f} km²")
    except Exception as e:
        st.warning(f"Couldn't calculate area: {str(e)}")

    if 'highway' in gdf.columns:
        st.write("\n🛣️ Street Statistics:")
        street_types = gdf['highway'].value_counts()
        st.write("Street types:")
        for street_type, count in street_types.items():
            st.write(f"- {street_type}: {count}")

        try:
            total_street_length = calculate_total_street_length(gdf)
            st.write(f"Total street length: {total_street_length:.2f} km")

            if area > 0:
                street_density = total_street_length / area
                st.write(f"Street density: {street_density:.2f} km/km²")
        except Exception as e:
            st.warning(f"Couldn't calculate street length: {str(e)}")

    # Calculate and display top 5 most common names
    top_names = gdf['name'].value_counts().head()
    st.write("\n📊 Top 5 most common names:")
    for name, count in top_names.items():
        st.write(f"- {name}: {count}")


def main():
    st.title("🌍 Davis's Fun Map Generator!")
    st.markdown("Generate and play with a fun map just like Davis would!  It works for any location worldwide!!")

    # Sidebar for location input and validation
    with st.sidebar:
        st.header("Location Input")
        location = st.text_input('Enter Location:', placeholder="e.g., New York City, Paris, Tokyo")
        location_type = st.selectbox('Select Type:', ['City', 'County', 'State', 'Country'])

        if st.button('🔍 Validate Location', key='validate'):
            with st.spinner('Validating location...'):
                validated_location = validate_location(location, location_type.lower())
            if validated_location:
                st.success('✅ Location validated successfully!')
                st.session_state.validated_location = validated_location
                st.json(validated_location)
            else:
                st.error('❌ Location not found. Please check your input.')

    # Main content area
    if 'validated_location' in st.session_state:
        st.header(f"Generate GeoJSON for {st.session_state.validated_location['display_name']}")

        col1, col2 = st.columns(2)

        with col1:
            if st.button('🗺️ Generate Boundary GeoJSON', key='boundary'):
                with st.spinner('Generating boundary GeoJSON...'):
                    geojson_data = generate_geojson_wrapper(st.session_state.validated_location, build_overpass_query)
                if geojson_data:
                    st.session_state.current_geojson = geojson_data
                    st.session_state.map_type = 'boundary'
                    st.success('✅ Boundary GeoJSON generated successfully!')

        with col2:
            if st.button('🛣️ Generate Streets GeoJSON', key='streets'):
                with st.spinner('Generating streets GeoJSON...'):
                    geojson_data = generate_geojson_wrapper(st.session_state.validated_location,
                                                           build_streets_overpass_query, streets_only=True)
                if geojson_data:
                    st.session_state.current_geojson = geojson_data
                    st.session_state.map_type = 'streets'
                    st.success('✅ Streets GeoJSON generated successfully!')

    # Display results if GeoJSON data is available
    if 'current_geojson' in st.session_state:
        st.header(
            f"📊 Analysis for {st.session_state.validated_location['display_name']} ({st.session_state.map_type.capitalize()})")

        tab1, tab2, tab3 = st.tabs(["🗺️ Map", "📋 Data Preview", "📈 Statistics"])

        with tab1:
            display_map(st.session_state.current_geojson)

        with tab2:
            display_data_preview(st.session_state.current_geojson)

        with tab3:
            display_statistics(st.session_state.current_geojson)

        st.download_button(
            label="📥 Download GeoJSON",
            data=json.dumps(st.session_state.current_geojson),
            file_name=f"{st.session_state.validated_location['display_name']}_{st.session_state.map_type}.geojson",
            mime="application/json",
            key='download_geojson'
        )


if __name__ == '__main__':
    main()
