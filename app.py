import streamlit as st
import json
import requests
from shapely.geometry import shape, Polygon, LineString, MultiPolygon
import folium
from streamlit_folium import st_folium
import geopandas as gpd

# Set page config
st.set_page_config(page_title="Davis's Fun Map Generator!", page_icon="🌍", layout="wide")

# Define color scheme
PRIMARY_COLOR = "#BB86FC"
SECONDARY_COLOR = "#03DAC6"
BACKGROUND_COLOR = "#121212"
TEXT_COLOR = "#E0E0E0"

# Custom CSS for dark mode and styling
st.markdown(f"""
    <style>
    body {{
        color: {TEXT_COLOR};
        background-color: {BACKGROUND_COLOR};
        font-family: 'Roboto', sans-serif;
    }}
    .stButton > button {{
        background-color: {PRIMARY_COLOR};
        color: {BACKGROUND_COLOR};
    }}
    .stDownloadButton > button {{
        background-color: {SECONDARY_COLOR};
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
    </style>
    """, unsafe_allow_html=True)

def make_request(url, params=None, headers=None):
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        st.error(f"API request failed: {str(e)}")
        return None

def validate_location(location, location_type):
    params = {'q': location, 'format': 'json', 'limit': 1, 'featuretype': location_type}
    headers = {'User-Agent': 'GeojsonGenerator/1.0'}
    response = make_request("https://nominatim.openstreetmap.org/search", params, headers)
    return response[0] if response else None

def generate_geojson(location, streets_only=False):
    area_id = int(location['osm_id']) + 3600000000 if location['osm_type'] == 'relation' else int(location['osm_id'])
    
    if streets_only:
        # Keep the streets query exactly as it was
        query = f"""
        [out:json];
        area({area_id})->.searchArea;
        (
          way["highway"](area.searchArea);
        );
        (._;>;);
        out geom;
        """
    else:
        # Updated query for boundary
        query = f"""
        [out:json];
        ({location['osm_type']}({location['osm_id']});
        >;
        );
        out geom;
        """
    
    response = make_request("http://overpass-api.de/api/interpreter", params={'data': query})
    if not response:
        return None, "Failed to get response from Overpass API"
    
    features = process_elements(response['elements'], streets_only)
    
    if features:
        gdf = gpd.GeoDataFrame.from_features(features)
        gdf = gdf.set_geometry('geometry')
        return json.loads(gdf.to_json()), None
    else:
        return None, f"No features found. Raw response: {json.dumps(response)}"

def process_elements(elements, streets_only):
    features = []
    ways = {e['id']: e for e in elements if e['type'] == 'way'}
    
    for element in elements:
        if element['type'] == 'way':
            coords = [(node['lon'], node['lat']) for node in element.get('geometry', [])]
            if len(coords) >= 2:
                geom = LineString(coords) if streets_only else (Polygon(coords) if coords[0] == coords[-1] else LineString(coords))
                features.append({
                    'type': 'Feature',
                    'geometry': geom.__geo_interface__,
                    'properties': element.get('tags', {})
                })
        elif element['type'] == 'relation' and not streets_only:
            outer_rings = []
            for member in element.get('members', []):
                if member['type'] == 'way' and member['role'] == 'outer':
                    way = ways.get(member['ref'])
                    if way:
                        coords = [(node['lon'], node['lat']) for node in way.get('geometry', [])]
                        if len(coords) >= 3 and coords[0] == coords[-1]:
                            outer_rings.append(Polygon(coords))
            if outer_rings:
                geom = outer_rings[0] if len(outer_rings) == 1 else MultiPolygon(outer_rings)
                features.append({
                    'type': 'Feature',
                    'geometry': geom.__geo_interface__,
                    'properties': element.get('tags', {})
                })
    return features


def display_map(geojson_data):
    gdf = gpd.GeoDataFrame.from_features(geojson_data['features'])
    bounds = gdf.total_bounds
    center = [(bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2]

    m = folium.Map(location=center, zoom_start=10, tiles="cartodbdark_matter")
    folium.GeoJson(
        geojson_data,
        style_function=lambda _: {'fillColor': PRIMARY_COLOR, 'color': SECONDARY_COLOR, 'weight': 2, 'fillOpacity': 0.7},
    ).add_to(m)

    m.fit_bounds([(bounds[1], bounds[0]), (bounds[3], bounds[2])])
    st_folium(m, width=700, height=500)

def display_data_preview(geojson_data):
    gdf = gpd.GeoDataFrame.from_features(geojson_data['features'])
    st.dataframe(gdf.drop(columns=['geometry']).head())

def display_statistics(geojson_data):
    gdf = gpd.GeoDataFrame.from_features(geojson_data['features'])
    gdf['geometry'] = gdf['geometry'].apply(shape)
    gdf = gdf.set_geometry('geometry')

    st.write("📊 General Statistics:")
    st.write(f"Total features: {len(gdf)}")
    st.write("Geometry types:")
    for geo_type, count in gdf.geometry.type.value_counts().items():
        st.write(f"- {geo_type}: {count}")

    if 'highway' in gdf.columns:
        st.write("\n🛣️ Street Statistics:")
        for street_type, count in gdf['highway'].value_counts().items():
            st.write(f"- {street_type}: {count}")

    if 'name' in gdf.columns:
        top_names = gdf['name'].value_counts().head()
        st.write("\n📊 Top 5 most common names:")
        for name, count in top_names.items():
            st.write(f"- {name}: {count}")

def main():
    st.title("🌍 Davis's Fun Map Generator!")
    st.markdown("Generate and play with a fun map just like Davis would! It works for any location worldwide!!")

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

    if 'validated_location' in st.session_state:
        st.header(f"Generate GeoJSON for {st.session_state.validated_location['display_name']}")

        col1, col2 = st.columns(2)

        with col1:
            if st.button('🗺️ Generate Boundary GeoJSON', key='boundary'):
                with st.spinner('Generating boundary GeoJSON...'):
                    geojson_data, error_message = generate_geojson(st.session_state.validated_location)
                if geojson_data:
                    st.session_state.current_geojson = geojson_data
                    st.session_state.map_type = 'boundary'
                    st.success('✅ Boundary GeoJSON generated successfully!')
                else:
                    st.error(f'❌ Failed to generate Boundary GeoJSON. {error_message}')

        with col2:
            if st.button('🛣️ Generate Streets GeoJSON', key='streets'):
                with st.spinner('Generating streets GeoJSON (this may take a while for large areas)...'):
                    geojson_data, error_message = generate_geojson(st.session_state.validated_location, streets_only=True)
                if geojson_data:
                    st.session_state.current_geojson = geojson_data
                    st.session_state.map_type = 'streets'
                    st.success('✅ Streets GeoJSON generated successfully!')
                else:
                    st.error(f'❌ Failed to generate Streets GeoJSON. {error_message}')

    if 'current_geojson' in st.session_state:
        st.header(f"📊 Analysis for {st.session_state.validated_location['display_name']} ({st.session_state.map_type.capitalize()})")

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