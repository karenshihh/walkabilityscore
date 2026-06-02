##philadelphia walkability calculator# #


## imports: sys for crashing, pandas reads excel, osmnx + networkx for the street  ##
## network, pyproj for coordinate transforms (web mercator <-> lon/lat) $$
import sys
import pandas as pd
import osmnx as ox
import networkx as nx
from pyproj import Transformer


##loading excel files##
EXCEL_FILE = "Vizcomp master dataset.xlsx"   
 
HOUSING  = "Affordable_Housing "
MEAL     = "Free_Meal_Sites"
GARDENS = "Registered_Community_Gardens"
MARKETS  = "Farmers_Markets"


##variable conversions and definitions##
max_miles  = 1.50
#according to google ai overview, most humans can walk 0.25 miles in about 5 minutes at an average pace##
meters_per_mile = 1609.34                       
max_walking_meters = max_miles * meters_per_mile


##converting coordinates from web mercator to longitude and latitude ##
to_lonlat = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True) 
 
def convert(x, y):
    try:
        lon, lat = to_lonlat.transform(float(x), float(y))
        return lon, lat
    except Exception:
        return None, None

def load_sheet(sheet_name, xcol="X", ycol="Y"):
    df = pd.read_excel(EXCEL_FILE, sheet_name=sheet_name)

    # Convert coordinates row by row
    lonlat = df.apply(lambda r: pd.Series(convert(r[xcol], r[ycol])), axis=1)
    lonlat.columns = ["lon", "lat"]
    df = pd.concat([df, lonlat], axis=1)
 
    # drop rows where conversion failed
    bad = df["lon"].isna() | df["lat"].isna()
    if bad.sum() > 0:
        print(f"  Note: dropped {bad.sum()} rows from '{sheet_name.strip()}' with invalid coordinates")
        df = df[~bad].copy()
 
    # drop rows outside Philadelphia's rough bounds (instead of just warning)
    in_bounds = (
        df["lon"].between(-75.35, -74.85) &
        df["lat"].between(39.80, 40.20)
    )
    if (~in_bounds).sum() > 0:
        print(f"  Note: dropped {(~in_bounds).sum()} rows from '{sheet_name.strip()}' outside Philadelphia bounds")
    df = df[in_bounds].copy()
 
    return df

##load data from excel sheets and convert coordinates##

print("Loading data from Excel workbook")

housing  = load_sheet(HOUSING)    
gardens  = load_sheet(GARDENS)   
markets  = load_sheet(MARKETS)   
 
## Meal sites special handling##
## coordinates are already long/lat, switched xys, and dropped the rows that have missing coordinates##

meals = pd.read_excel(EXCEL_FILE, sheet_name=MEAL)
meals = meals.dropna(subset=["x", "y"])
meals = meals.rename(columns={"x": "lat", "y": "lon"})
meals = meals[
    meals["lon"].between(-75.35, -74.85) &
    meals["lat"].between(39.80, 40.20)
].copy()
 
print(f"  Affordable housing projects : {len(housing)}")
print(f"  Meal sites (with coords)    : {len(meals)}")
print(f"  Community gardens           : {len(gardens)}")
print(f"  Farmers markets             : {len(markets)}")


## now download street network will take a long time to load but hopefully will cache after first initial run ##
print("\nDownloading Philadelphia pedestrian street network from OpenStreetMap")
 
G = ox.graph_from_place(
    "Philadelphia, Pennsylvania, USA",
    network_type="walk"     # pedestrian paths only, no highways or car-only roads
)
 
print(f"Network ready: {len(G.nodes):,} nodes, {len(G.edges):,} edges")

##okay this is the actual scoring part, we are going to calculate the shortest walking distance from each point of interest to the nearest node in the street network, and then assign points based on how far it is this is also subject to change based on everything else and everyone's thoughts##

def score(walk_meters):
    # Convert meters to miles, return points based on distance
    miles = walk_meters / meters_per_mile
    if miles <= 0.25: return 3.0   ##5 min walk##
    if miles <= 0.50: return 2.5   ##10 min walk##
    if miles <= 0.75: return 2.0   ##15 min walk##
    if miles <= 1.00: return 1.5   ##20 min walk##
    if miles <= 1.25: return 1.0   ##25 min walk##
    if miles <= 1.50: return 0.5   ##30 min walk##
    return 0.0                     ##>30 min walk##
 
 
def label(total): ##assigns a label based on the total score##
    if total >= 7.0: return "Excellent"
    if total >= 5.0: return "Good"
    if total >= 3.0: return "Fair"
    if total >= 0.1: return "Poor"  
    return "Very Poor"
 
print("\nSnapping all points to the street network") 

##basically this is using dots as stand ins for addresses that may not be at an intersection, the dot is now used in the routing, when every point is snapped in it's going to be easier to calculate and the difference is really just a few meters so pretty negligible##
 
def snap(lon, lat):
    # Returns the ID of the nearest street intersection to this point
    return ox.distance.nearest_nodes(G, lon, lat)
 
def snap_dataset(df, name_col):
    # Snaps every row and returns a list of (node_id, display_name) tuples
    return [
        (snap(row["lon"], row["lat"]), str(row[name_col]))
        for _, row in df.iterrows()
    ]
 
meal_nodes = snap_dataset(meals,   "site_name")
gd_nodes   = snap_dataset(gardens, "garden_name")
mk_nodes   = snap_dataset(markets, "name")
 
print("  Done.")

##now we're getting to the good part we now calculate the nearest amenity for each housing project and assign points based on the distance##

def nearest_amenity(dist_map, amenity_nodes):
    best_meters = float("inf")
    best_name = ""
 
    for amenity_node, name in amenity_nodes:
        meters = dist_map.get(amenity_node)
        if meters is None:        # not reachable within cutoff
            continue
        if meters < best_meters:
            best_meters = meters
            best_name = name
 
    if best_meters > max_walking_meters:
        return 0.0, "", None
 
    return score(best_meters), best_name, round(best_meters / meters_per_mile, 3)

##now this scores the housing projects ##

print("\nScoring housing projects")
rows = []
 
housing_nodes = [
    snap(row["lon"], row["lat"])
    for _, row in housing.iterrows()
]
 
for i, ((_, h), h_node) in enumerate(zip(housing.iterrows(), housing_nodes), 1):
    name = h.get("project_name", f"Project {i}")
    # one bounded shortest-path search from this house, capped at max walking distance
    dist_map = nx.single_source_dijkstra_path_length(
        G, h_node, cutoff=max_walking_meters, weight="length"
    )
 
    score_ml, nearestname_ml, nearestdistance_ml = nearest_amenity(dist_map, meal_nodes)
    score_gd, nearestname_gd, nearestdistance_gd = nearest_amenity(dist_map, gd_nodes)
    score_mk, nearestname_mk, nearestdistance_mk = nearest_amenity(dist_map, mk_nodes)
 
    total = score_ml + score_gd + score_mk
 
    print(f"  [{i}/{len(housing)}] {name} -- {total}")
 
    rows.append({
        "project_name": h.get("project_name", ""),
        "address": h.get("address", ""),
        "status": h.get("status", ""),
        "total_units": h.get("total_units", ""),
        "lon": round(h["lon"], 6),
        "lat": round(h["lat"], 6),
 
        "meal_sites_score": score_ml,
        "meal_sites_nearest": nearestname_ml,
        "meal_sites_walk_mi": nearestdistance_ml,
 
        "gardens_score": score_gd,
        "gardens_nearest": nearestname_gd,
        "gardens_walk_mi": nearestdistance_gd,
 
        "markets_score": score_mk,
        "markets_nearest": nearestname_mk,
        "markets_walk_mi": nearestdistance_mk,
 
        "walkability_total": total,
        "walkability_label": label(total),
    })


output = pd.DataFrame(rows).sort_values("walkability_total", ascending=False)

output["total_units"] = pd.to_numeric(output["total_units"], errors="coerce")
 
output.to_csv("affordable_housing_walkability.csv", index=False)
 
##build the summary report as a list of lines, then write it all to a text file ##
lines = []
 
lines.append("PHILADELPHIA AFFORDABLE HOUSING WALKABILITY -- SUMMARY REPORT")
lines.append(f"Housing projects scored: {len(output)}")
 
lines.append("\n-- Score label breakdown -----------------------------")
lines.append(output["walkability_label"].value_counts().to_string())
 
lines.append("\n-- Average score per category ------------------------")
for col in ["meal_sites_score", "gardens_score", "markets_score"]:
    lines.append(f"  {col:22s}: {output[col].mean():.2f} avg")
 
lines.append("\n-- Top 10 most walkable housing projects -------------")
lines.append(output[[
    "project_name", "walkability_total", "walkability_label",
    "meal_sites_score", "gardens_score", "markets_score"
]].head(10).to_string(index=False))
 
lines.append("\n-- Top 10 least walkable housing projects -------------")
# many projects tie at the bottom (total 0), so among equal totals show the largest buildings first -- those are the highest-impact access gaps ##
worst = output.sort_values(
    ["walkability_total", "total_units"],
    ascending=[True, False]
)
lines.append(worst[[
    "project_name", "walkability_total", "walkability_label",
    "meal_sites_score", "gardens_score", "markets_score", "total_units"
]].head(10).to_string(index=False))
 
lines.append("\n-- Zero-access housing (no amenity within 1.5 mi) ----")
# score of exactly 0 = nothing reachable in ANY category. Worth calling out separately, since these are total-isolation cases, not just low scorers. ##
zero_access = output[output["walkability_total"] == 0]
zero_units = zero_access["total_units"].sum()
total_units = output["total_units"].sum()
lines.append(f"  Projects with zero access : {len(zero_access)} of {len(output)}")
lines.append(f"  Units in those projects   : {zero_units:.0f} of {total_units:.0f} "
             f"({zero_units / total_units * 100:.1f}%)")
 
lines.append("\n-- Distribution of total scores ----------------------")
lines.append(output["walkability_total"].describe().to_string())
lines.append(f"  Median: {output['walkability_total'].median()}")
 
lines.append("\n-- Nearest-amenity distances (reachable only) --------")
for col in ["meal_sites_walk_mi", "gardens_walk_mi", "markets_walk_mi"]:
    lines.append(f"  {col:20s}: median {output[col].median():.2f} mi, "
                 f"unreachable {output[col].isna().sum()} houses")
 
lines.append("\n-- Most-frequently-nearest meal sites ----------------")
lines.append(output.loc[output["meal_sites_nearest"] != "", "meal_sites_nearest"]
             .value_counts().head(10).to_string())
 
lines.append("\n-- Units vs walkability correlation ------------------")
lines.append(output[["total_units", "walkability_total"]].corr().to_string())
 
# write the whole report to a text file ##
report = "\n".join(lines)
with open("walkability_summary.txt", "w", encoding="utf-8") as f:
    f.write(report)
print(f"\nDone!")
print(f"  Full results : affordable_housing_walkability.csv ({len(output)} rows)")
print(f"  Summary stats: walkability_summary.txt")



