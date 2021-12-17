from flask import Flask, render_template, request, redirect, send_from_directory, url_for
from flask_apscheduler import APScheduler
from flask_table import Table, Col, LinkCol, create_table
from dateutil.parser import isoparse
from dateutil.tz import gettz
from math import cos, atan, pi, sqrt, log10, floor
from os.path import exists
from io import TextIOWrapper as textwrap
from sys import getsizeof
from functools import cmp_to_key
import zipfile
import datetime as dt
import time
import requests
import re
import csv
from urllib.parse import quote
from fuzzywuzzy import fuzz
import pandas as pd
from numpy import argsort
from collections import Counter

depStatus = {
    "onTime": "On time",
    "ontime": "On time",
    "early": "Early",
    "delayed": "Late",
    "cancelled": "Cancelled"
}

with open("api.key", "r") as f:
    apikey = f.read().strip()

headers = {
    "accept": "application/json",
    "x-api-key": apikey
}

Rm2 = (6371.009 * 1000)**2

patz = gettz("Pacific/Auckland")

depurl = "https://api.opendata.metlink.org.nz/v1/stop-predictions"
stoplisturl = "https://api.opendata.metlink.org.nz/v1/gtfs/stops"
routelisturl = "https://api.opendata.metlink.org.nz/v1/gtfs/routes"
feedinfourl = "https://api.opendata.metlink.org.nz/v1/gtfs/feed_info"
stoptimesurl = "https://api.opendata.metlink.org.nz/v1/gtfs/stop_times"
alertsurl = "https://api.opendata.metlink.org.nz/v1/gtfs-rt/servicealerts"
positionurl = "https://api.opendata.metlink.org.nz/v1/gtfs-rt/vehiclepositions"
zipurl = "https://static.opendata.metlink.org.nz/v1/gtfs/full.zip"
tripupdatesurl = "https://api.opendata.metlink.org.nz/v1/gtfs-rt/tripupdates"

dayShort = {1: 'M', 2: 'Tu', 3: 'W', 4: 'Th', 5: 'F', 6: 'Sa', 7: 'Su'}
directions = {"N": "North", "NE": "North East", "E": "East",
              "SE": "South East", "S": "South", "SW": "South West",
              "W": "West", "NW": "North West"}

stopinfo = []
stopids = {}
feedinfo = {}
zipinfo = {}
stopnames = {}
routelist = {}
servroute = {}
triplist = []
alltrips = []
alertlist = []
routetrips = {}
trip_serv = {}
caldates = {}
agencies = {}
trip_seq = {}
trip_dir = {}
trip_stop_times = {}
trip_positions = {}
trip_updates = {}
trip_sid = {}
stop_patterns = {}
stoplastupdate = dt.datetime.now(patz) - dt.timedelta(days=14)
routeslastupdate = dt.datetime.now(patz) - dt.timedelta(days=14)
alertslastupdate = dt.datetime.now(patz) - dt.timedelta(seconds=60*20)
positionlastupdate = dt.datetime.now(patz) - dt.timedelta(seconds=60*20)

def servfromtrip(trip_id, ag_ids):
    for ag in ag_ids:
        agpos = trip_id.find("__" + ag + "__")
        if agpos == -1:
            continue
        remd = trip_id[agpos + len(ag) + 4:]
        ext = ""
        if re.search("[^_]_\d$", remd) is not None:
            ext = remd[-2:]
            remd = remd[:-2]
        if ag in ["RAIL", "WCCL", "EBYW"]:
            return remd.replace("_", " ") + ext
            #remd = remd.replace("_", " ")
            #return remd.replace("  ", "_")
        else:
            return remd[:int(len(remd) / 2 - 1)].replace("__", "_") + ext
    return None


def downloadZipDataset():
    print("Downloading zip of GTFS metadata")
    req = requests.get(zipurl, timeout=10)
    with open("GTFS_full.zip", 'wb') as df:
        for chunk in req.iter_content(chunk_size=128):
            df.write(chunk)
        return True
    return False

def loadZipDataset():
    global triplist
    global alltrips
    global zipinfo
    global routelist
    global routetrips
    global stopinfo
    global stopids
    global stopnames
    global servroute
    global routeslastupdate
    global stoplastupdate
    global trip_serv
    global caldates
    global agencies
    global trip_seq
    global trip_dir
    global trip_sid
    global trip_stop_times
    global stop_patterns
    nowtime = dt.datetime.now(patz)
    print("Loading zip of metadata at {}".format(nowtime.strftime("%c")))
    if not exists("GTFS_full.zip"):
        return False
    with zipfile.ZipFile("GTFS_full.zip") as z:
        znames = z.namelist()
        if not all(needed_file in znames for needed_file in ["feed_info.txt",
                                                             "trips.txt",
                                                             "routes.txt",
                                                             "stops.txt",
                                                             "agency.txt",
                                                             "stop_times.txt",
                                                             "calendar_dates.txt",
                                                             "stop_patterns.txt",
                                                             "stop_pattern_trips.txt"]):
            return False

        with textwrap(z.open("agency.txt"), encoding="utf-8-sig") as agfile:
            agencies = {}
            agrows = csv.DictReader(agfile)
            for row in agrows:
                agencies[row["agency_id"]] = row
        if len(agencies) == 0:
            return False
        print("done agencies")

        with textwrap(z.open("trips.txt"), encoding="utf-8-sig") as tripfile:
            triplist = []
            triprows = csv.DictReader(tripfile)
            for row in triprows:
                triplist.append(row)
            alltrips = [trip["trip_id"] for trip in triplist]
            trip_dir = {trip["trip_id"]: trip["direction_id"] for trip in
                        triplist}
        if len(triplist) == 0:
            return False
        print("done trips")

        with textwrap(z.open("stops.txt"), encoding="utf-8-sig") as stopfile:
            stopinfo = []
            stoprows = csv.DictReader(stopfile)
            for row in stoprows:
                row["stop_lat"] = float(row["stop_lat"])
                row["stop_lon"] = float(row["stop_lon"])
                stopinfo.append(row)
            stopids = {x["stop_id"]: ind for ind, x in enumerate(stopinfo)}
            stopnames = {x["stop_name"]: x["stop_id"] for x in stopinfo}
        if len(stopinfo) == 0:
            return False
        else:
            stoplastupdate = nowtime
        print("done stops")

        with textwrap(z.open("stop_times.txt"),
                      encoding="utf-8-sig") as tsfile:
            tsrows = csv.DictReader(tsfile)
            trip_stop_times = {}
            for row in tsrows:
                nval = {"id": row["stop_id"],
                        "time": row["departure_time"],
                        "tp": True if row["timepoint"] == "1" else False,
                        "seq": row["stop_sequence"],
                        "sind": stopids.get(row["stop_id"])}
                if row["trip_id"] in trip_stop_times:
                    trip_stop_times[row["trip_id"]].append(nval)
                else:
                    trip_stop_times[row["trip_id"]] = [nval]
        print("done stop times")

        with textwrap(z.open("routes.txt"), encoding="utf-8-sig") as routefile:
            routeinfo = []
            routerows = csv.DictReader(routefile)
            for row in routerows:
                routeinfo.append(row)
            routelist = {x["route_short_name"]: x for x in routeinfo}
            servroute = {x["route_id"]: x["route_short_name"] for x in routeinfo}
            routetrips = {r: [t["trip_id"] for t in triplist if t["route_id"]
                              == r] for r in [rv["route_id"] for rv in
                                              routeinfo]}
        if len(routelist) == 0:
            return False
        else:
            routeslastupdate = nowtime
        print("done routes")

        with textwrap(z.open(
            "calendar_dates.txt"), encoding="utf-8-sig") as calfile:
            calrows = csv.DictReader(calfile)
            caldates = {}
            for row in calrows:
                if row["exception_type"] != "1":
                    continue
                elif row["service_id"] in caldates:
                    caldates[row["service_id"]].append(dt.datetime.strptime(row["date"],
                                                                          "%Y%m%d"))
                else:
                    caldates[row["service_id"]] = [dt.datetime.strptime(row["date"], "%Y%m%d")]
        if len(caldates) == 0:
            return False
        print("done calendar")

        with textwrap(z.open(
            "stop_pattern_trips.txt"), encoding="utf-8-sig") as spfile:
            trip_serv = {}
            trip_seq = {}
            trip_sid = {}
            sptrows = csv.DictReader(spfile)
            for row in sptrows:
                trip_serv[row["trip_id"]] = servfromtrip(row["trip_id"],
                                                         agencies.keys())
                trip_seq[row["trip_id"]] = row["trip_sequence"]
                trip_sid[row["trip_id"]] = row["stop_pattern_id"]
        if len(trip_serv) == 0:
            return False
        print("done stop pattern/trips")

        with textwrap(z.open(
            "stop_patterns.txt"), encoding="utf-8-sig") as spfile:
            stop_patterns = {}
            sptrows = csv.DictReader(spfile)
            for row in sptrows:
                nval = {"id": row.get("stop_id"),
                        "seq": row.get("stop_sequence"),
                        "sind": stopids.get(row.get("stop_id")),
                        "timepoint": row.get("timepoint") == "1"}
                if row["stop_pattern_id"] in stop_patterns:
                    stop_patterns[row["stop_pattern_id"]].append(nval)
                else:
                    stop_patterns[row["stop_pattern_id"]] = [nval]
        if len(stop_patterns) == 0:
            return False
        print("done stop patterns")

        with textwrap(z.open("feed_info.txt"), encoding="utf-8-sig") as feedfile:
            feedrows = csv.DictReader(feedfile)
            for row in feedrows:
                zipinfo = row

    if "feed_version" not in zipinfo:
        return False
    print("Zip file loaded at {}".format(dt.datetime.now(patz).strftime("%c")))
    return True


def updateFeedInfo(force=False):
    global feedinfo
    nowtime = dt.datetime.now(patz)
    tstoday = nowtime.strftime("%Y%m%d")
    if force or "feed_end_date" not in feedinfo or feedinfo["feed_end_date"] < tstoday:
        req = requests.get(feedinfourl, headers=headers, timeout=10)
        if req.status_code != 200:
            print("Failed to update feed_info metadata at {}.".format(
                nowtime.strftime("%c")))
            feedinfo["feed_end_date"] = tstoday
            feedinfo["feed_start_date"] = tstoday
            return
        feedinfo = req.json()[0]
        print("Updated feed_info metadata at {}, new expiry date: {}.".format(
            nowtime.strftime("%c"), feedinfo["feed_end_date"]))
    # If data file not previously loaded, load it
    if "feed_version" not in zipinfo:
        # If the file doesn't exist, download it - stop on fail
        if not exists("GTFS_full.zip"):
            print("No metadata file, downloading")
            if not downloadZipDataset():
                return
        if not loadZipDataset():
            return
    # If data file is out of date, redownload it and reload it
    if (zipinfo["feed_start_date"] < feedinfo["feed_start_date"] or
        zipinfo["feed_end_date"] < feedinfo["feed_end_date"]):
        print("Old metadata file, downloading")
        if not downloadZipDataset():
            return
        loadZipDataset()


def updateAlerts(force=False):
    global alertlist
    global alertslastupdate
    nowtime = dt.datetime.now(patz)
    if force or (nowtime - alertslastupdate).seconds >= 60 * 5:
        req = requests.get(alertsurl, headers=headers, timeout=10)
        if req.status_code != 200:
            return
        talerts = req.json()
        if "entity" not in talerts:
            return
        alertlist = [{
            "effect": a["alert"].get("effect").lower().replace("_", "-") if "effect" in
                a["alert"] else None,
            "cause": a["alert"].get("cause").lower().replace("_", "-") if "cause" in a["alert"]
                else None,
            "severity": a["alert"].get("severity_level").lower().replace(
                "_", "-") if "severity_level" in a["alert"] else None,
            "desc":
            a["alert"]["description_text"]["translation"][
                0].get("text").replace("\r", " ").replace("\n", " ") if
                "description_text" in a["alert"] and "translation" in a["alert"]["description_text"]
                and len(a["alert"]["description_text"]["translation"]) > 0 else None,
            "head": a["alert"]["header_text"]["translation"][
                0].get("text").replace("\r", " ").replace("\n", " ") if
                "header_text" in a["alert"] and "translation" in a["alert"]["header_text"]
                and len(a["alert"]["header_text"]["translation"]) > 0 else None,
            "routes": [servroute[e["route_id"]] for e in a["alert"]["informed_entity"] if
                       "route_id" in e and e["route_id"] in servroute] if "informed_entity" in a["alert"] else [],
            "stops": [e["stop_id"] for e in a["alert"]["informed_entity"] if
                       "stop_id" in e] if "informed_entity" in a["alert"] else [],
            "trips": [e["trip"].get("trip_id") for e in a["alert"]["informed_entity"] if
                       "trip" in e and "trip_id" in e["trip"] and
                      e["trip"]["trip_id"] in alltrips] if "informed_entity" in a["alert"] else [],
            "start":
                dt.datetime.fromtimestamp(a["alert"]["active_period"][0][
                    "start"], patz)
                if "active_period" in a["alert"] and
                len(a["alert"]["active_period"]) > 0 and "start" in
                a["alert"]["active_period"][0] else
                None,
            "end":
                dt.datetime.fromtimestamp(a["alert"]["active_period"][0][
                    "end"], patz)
                if "active_period" in a["alert"] and
                len(a["alert"]["active_period"]) > 0 and "end" in
                a["alert"]["active_period"][0] else
                None,
            "url": a["alert"]["url"]["translation"][0].get("text") if "url" in
                a["alert"] and "translation" in a["alert"]["url"] and
                len(a["alert"]["url"]["translation"]) > 0 else None,
            "id": a.get("id"),
            "timestamp": isoparse(a["timestamp"]) if "timestamp" in a else None
            } for a in talerts["entity"] if "alert" in a
        ]
        alertslastupdate = nowtime


def updatePositions():
    global trip_positions
    global positionlastupdate
    req = requests.get(positionurl, headers=headers, timeout=10)
    if req.status_code != 200:
        return
    posdata = req.json()
    if ("header" not in posdata or "entity" not in posdata or
        len(posdata.get("entity")) == 0 or "timestamp" not in
        posdata.get("header")):
        return
    datstamp = dt.datetime.fromtimestamp(posdata["header"]["timestamp"], patz)
    tpdict = {}
    for entity in posdata["entity"]:
        try:
            tid = entity["vehicle"]["trip"]["trip_id"]
            tpdict[tid] = {
                "direction": entity["vehicle"]["trip"]["direction_id"],
                "route_id": entity["vehicle"]["trip"]["route_id"],
                "start_time": entity["vehicle"]["trip"]["start_time"],
                "bearing": entity["vehicle"]["position"]["bearing"],
                "lat": entity["vehicle"]["position"]["latitude"],
                "lon": entity["vehicle"]["position"]["longitude"],
                "vehicle_id": entity["vehicle"]["vehicle"]["id"],
                "timestamp": dt.datetime.fromtimestamp(
                    entity["vehicle"]["timestamp"], patz)
            }
        except:
            print("Error handling vehicle entity")
            print(entity)
    if len(tpdict) > 0:
        seenveh = [tpdict[t]["vehicle_id"] for t in tpdict]
        keepovers = {t: trip_positions[t] for t in trip_positions if t not in
                     tpdict and (datstamp -
                                 trip_positions[t]["timestamp"]).seconds
                     < 60*5 and trip_positions[t]["vehicle_id"] not in seenveh}
        if len(keepovers) > 0:
            tpdict.update(keepovers)
        positionlastupdate = datstamp
        trip_positions = tpdict


def updateTripUpdates():
    global trip_updates
    req = requests.get(tripupdatesurl, headers=headers, timeout=10)
    if req.status_code != 200:
        return
    updata = req.json()
    if ("header" not in updata or "entity" not in updata or
        len(updata.get("entity")) == 0 or "timestamp" not in
        updata.get("header")):
        return
    datstamp = dt.datetime.fromtimestamp(updata["header"]["timestamp"], patz)
    updict = {}
    for entity in updata["entity"]:
        try:
            tup = entity.get("trip_update")
            tid = tup.get("trip").get("trip_id")
            delay = tup.get("stop_time_update").get("arrival").get("delay")
            s_r = tup.get("trip").get("schedule_relationship")
            tstamp = dt.datetime.fromtimestamp(tup.get("timestamp"), patz)
            vid = tup.get("vehicle").get("id")
            updict[tid] = {"delay": delay, "sr": s_r, "ts": tstamp, "vid": vid}
        except:
            print("Error handling update entity")
            print(entity)
    if len(updict) > 0:
        seenveh = [updict[t]["vid"] for t in updict]
        keepovers = {t: trip_updates[t] for t in trip_updates if t not in
                     updict and (datstamp -
                                 trip_updates[t]["ts"]).seconds
                     < 60*5 and trip_updates[t]["vid"] not in seenveh}
        if len(keepovers) > 0:
            updict.update(keepovers)
        trip_updates = updict



updateFeedInfo(True)
updateAlerts(True)
updatePositions()
updateTripUpdates()

def routeCodeKey(rc):
    if len(rc) < 2:
        return rc.zfill(4)
    fc = rc[0]
    lc = rc[-1]
    if fc.isnumeric():
        if lc.isnumeric():
            return rc.zfill(4)
        else:
            return rc[:-1].zfill(4) + lc
    else:
        if lc.isnumeric():
            return rc.rjust(4, '9')
        elif rc[1:-1].isnumeric():
            return fc + rc[1:-1].zfill(2) + lc
        else:
            return rc


def sortedRouteCodes():
    rcodes = list(routelist.keys())
    if rcodes is None or len(rcodes) == 0:
        return None
    rcodes.sort(key=routeCodeKey)
    return rcodes


def planeDistance2(lat1, lon1, lat2, lon2):
    rlat1 = pi/180 * lat1
    rlon1 = pi/180 * lon1
    rlat2 = pi/180 * lat2
    rlon2 = pi/180 * lon2
    pm = (rlat1 + rlat2)/2
    dlat = rlat1 - rlat2
    dlon = rlon1 - rlon2
    return Rm2 * (dlat**2 + (cos(pm) * dlon)**2)


def headingdeg(bearing):
    br = bearing % 360
    if br < 45*0.5:
        return "N"
    if br < 45*1.5:
        return "NE"
    if br < 45 * 2.5:
        return "E"
    if br < 45 * 3.5:
        return "SE"
    if br < 45 * 4.5:
        return "S"
    if br < 45 * 5.5:
        return "SW"
    if br < 45 * 6.5:
        return "W"
    if br < 45 * 7.5:
        return "NW"
    return "N"


def heading(dlat, dlon):
    if dlon == 0:
        if dlat > 0:
            return "N"
        else:
            return "S"
    adeg = atan(dlat / dlon) * 180 / pi
    if dlon > 0:
        if adeg > 45*1.5:
            return "N"
        if adeg > 45*0.5:
            return "NE"
        if adeg > -45*0.5:
            return "E"
        if adeg > -45*1.5:
            return "SE"
        return "S"
    else:
        if adeg > 45*1.5:
            return "S"
        if adeg > 45*0.5:
            return "SW"
        if adeg > -45*0.5:
            return "W"
        if adeg > -45*1.5:
            return "NW"
        return "N"


def footerData():
    return {
        "alerts": len(alertlist),
        "vehicles": len(trip_positions)
    }


def prettyDistance(dist, fig=1):
    return floor(round(dist, fig - 1 -floor(log10(dist))))


def tripTimeTable(tripData, routeCode, tableID, timepoints_only = False):
    if len(tripData) == 0:
        return None
    trip_ids = [trip["trip_id"] for trip in tripData]

    trip_times = {tid: trip_stop_times[tid] for tid in trip_ids if tid in
                  trip_stop_times}
    dupes = set()
    for tid in trip_times:
        counter = dict(Counter([x["sind"] for x in
                                trip_times[tid]]))
        dupes = dupes.union([x for x in counter if counter[x] > 1])
        for i in range(0, len(trip_times[tid])):
            trip_times[tid][i]["pin"] = 0

    print(dupes)
    for tid in trip_times:
        duinds = [ind for ind, x in enumerate(trip_times[tid]) if x["sind"] in dupes]
        for ind in duinds:
            trip_times[tid][ind]["pin"] = ind + 2
        if trip_times[tid][0]["sind"] in dupes:
            trip_times[tid][0]["pin"] = -1
        if trip_times[tid][-1]["sind"] in dupes:
            trip_times[tid][-1]["pin"] = 1

    start_times = {tid: trip_times[tid][0].get("time") for tid in trip_times}
    trip_ids = [tid for tid in sorted(trip_ids, key=lambda x:
                                      start_times.get(x)) if tid in
                trip_stop_times]
    long_d = [{
        "trip_id": trip_id,
        "stop_id": stopt.get("id"),
        "pin": stopt.get("pin"),
        "sind": stopt.get("sind"),
        "seq": stopt.get("seq"),
        "time": stopt.get("time"),
        "tp": stopt.get("tp")}
        for trip_id in trip_times for stopt in trip_times[trip_id] if
        stopt.get("tp") or not timepoints_only]
    trip_long = pd.DataFrame(long_d)
    trip_p = trip_long.pivot(index = ["stop_id", "sind", "pin"],
                             columns = "trip_id",
                             values = ["seq", "time", "tp"])


    #trip_p.sort_values(list(zip(["time"] * len(trip_ids), trip_ids)),
    #                   key=cmp_to_key(cmpttrows), inplace=True)
    for trip_id in trip_ids:
        trip_p[("time", trip_id)] = trip_p[("time", trip_id)].fillna('')
        trip_p[("tp", trip_id)] = trip_p[("tp", trip_id)].fillna(False)
    seqcols = trip_p.loc[:, [("seq", trip_id) for trip_id in trip_ids]]
    trip_p["orderer"] = seqcols.apply(lambda x: max(
        [int(y) for y in x if not pd.isna(y)]), axis=1)
    trip_p["atp"] = trip_p.loc[:, [("tp", trip_id) for trip_id in
                                   trip_ids]].apply(any, axis=1)

    trip_p.sort_values(["orderer", "pin"], inplace=True)
    trip_p.reset_index(inplace=True)
    trip_p.columns = [''.join(col).strip() for col in trip_p.columns.values]

    relcols = ["time" + tid for tid in trip_ids]

    def cmpttrows(rowi1, rowi2):
        row1 = trip_p.loc[:, relcols].iloc[rowi1].replace('', pd.NA)
        row2 = trip_p.loc[:, relcols].iloc[rowi2].replace('', pd.NA)
        #print(pd.DataFrame([row1, row2, row1 > row2, row1 < row2]))
        #print(row1[6])
        #print(row2[6])
        #print(any(row1 > row2))
        #print(any(row1 < row2))
        #print(row1[6] > row2[6])
        #print(row1[6] < row2[6])
        #print()
        if any(row1 > row2):
            return 1
        elif any(row1 < row2):
            return -1
        else:
            return 0

    excols = list(range(0, trip_p.shape[0]))
    print(excols)
    excols.sort(key=cmp_to_key(cmpttrows))
    print(excols)
    print(argsort(excols))
    #trip_p["fullsort"] = pd.Series(excols)
    trip_p["fullsort"] = argsort(excols)
    trip_p.sort_values("fullsort", inplace=True)
    trip_p["names"] = [stopinfo[sid]["stop_name"] for sid in trip_p["sind"]]
    trip_p["sms"] = [stopinfo[sid]["parent_station"] if
                     stopinfo[sid]["parent_station"] != "" else
                     stopinfo[sid]["stop_id"] for sid in trip_p["sind"]]
    trip_p["stop_id"] = trip_p["sms"]
    trip_p["zone"] = [stopinfo[sid]["zone_id"] for sid in trip_p["sind"]]
    trip_p["rowid"] = trip_p.apply(lambda x:
                                   "{}-stop-{}".format(tableID, x["sms"]) if
                                      x["pin"] == 0 else
                                      "{}-stop-{}-{}".format(tableID,
                                                             x["sms"],
                                                          x["pin"]),
                                      axis=1)
    tt_dict = trip_p.to_dict('records')

    table_spec = create_table(base=TimeTableBase)

    for trip in trip_ids:
        table_spec = table_spec.add_column("time" + trip,
                                           Col(start_times.get(trip)[:5],
                                               td_html_attrs = {"class": "tcol"}))
    tt_draft = table_spec(tt_dict)
    tbody = tt_draft.tbody()
    theader = "<th>Code</th>\n<th>Stop</th><th></th>\n<th>Zone</th>\n" + "\n".join([
                "<th><a href='/route/{}/?trip={}'>{}</a></th>".format(
                    quote(routeCode, safe=""), quote(tid, safe=""),
                    start_times.get(tid)[:5]) for tid in trip_ids])
    ttable = ("<table id='{}' class='cleantable full-timetable'>"
              "<thead>\n{}\n</thead>\n{}\n</table>").format(tableID,
                                               theader, tbody)
    return(ttable)


class TimeTable(Table):
    routeCol = LinkCol("Route", "routeInfo", th_html_attrs={"title": "Route"},
                    url_kwargs=dict(rquery="rname", trip="trip_id"),
                       attr='route')
    dest = Col("Dest", th_html_attrs={"title": "Destination"})
    sched = Col("Sched", th_html_attrs={"title": "Scheduled departure time"})
    status = Col("Status", th_html_attrs={"title": "Status"})
    est = Col("Est", th_html_attrs={"title": "Estimated time until departure"})
    table_id = "stoptimetable"
    classes = ["cleantable"]


class StopTable(Table):
    code = LinkCol("Code", "timetable", url_kwargs=dict(stop="sms"),
                   attr='code')
    stop = Col("Stop")
    zone = Col("Zone", td_html_attrs = {"class": "centrecol"})
    table_id = "stoptable"
    classes = ["cleantable"]


class StopTimeTable(Table):
    code = LinkCol("Code", "timetable",
                   url_kwargs=dict(stop="sms"),
                   attr='code')
    stop = Col("Stop")
    sched = Col("Sched", td_html_attrs = {"class": "tcol"})
    zone = Col("Zone", td_html_attrs = {"class": "centrecol"})
    table_id = "stoptimetable"
    classes = ["cleantable"]

    def get_tr_attrs(self, item):
        trattrs = {'id': "stop-{}".format(item["code"])}
        if item["tp"] and item["nearest"]:
            trattrs.update({'class': 'timepoint nearest'})
        elif item["tp"]:
            trattrs.update({'class': 'timepoint'})
        elif item["nearest"]:
            trattrs.update({'class': 'nearest'})
        return trattrs


class LocationTable(Table):
    code = LinkCol("Code", "timetable", url_kwargs=dict(stop="sms"),
                   attr='code')
    stop = Col("Stop")
    zone = Col("Zone", td_html_attrs = {"class": "centrecol"})
    distance = Col("Distance")
    table_id = "locationtable"
    classes = ["cleantable"]


class DateTable(Table):
    day = Col("Day")
    date = Col("Date", td_html_attrs = {"class": "datecol"})
    table_id = "datetable"
    classes = ["cleantable"]


class TripTable(Table):
    vehcol = LinkCol("Vehicle ID", "routeInfo",
                     url_kwargs=dict(rquery="rname", trip="trip_id"),
                     attr="vehicle",
                     th_html_attrs={"title": "Assigned vehicle id"},
                     td_html_attrs={"class": "centrecol"})
    direction = Col("Direction",
                    th_html_attrs={"title": "Direction of trip"},
                    td_html_attrs={"class": "centrecol"})
    departed = Col("Departed",
                    th_html_attrs={"title":
                                   "Time of departure from initial stop"},
                    td_html_attrs={"class": "centrecol"})
    table_id = "triptable"
    classes = ["cleantable"]


class SelfAnchorCol(Col):
    def td_contents(self, item, attr_list):
        return "<a href='#{}'>{}</a>".format(
            item["rowid"], attr_list["text"]
        )

class TimeTableBase(Table):
    stop_id = LinkCol("Code", "timetable",
                      url_kwargs=dict(stop="sms"), attr='stop_id')
    names = Col("Stop")
    anch = SelfAnchorCol("sdf", attr_list = dict(text="§"),
                         td_html_attrs = {"class": "ac"})
    zone = Col("Zone", td_html_attrs = {"class": "centrecol"})
    
    def get_tr_attrs(self, item):
        tratts = {"id": item["rowid"]}
        if item.get("atp"):
            tratts.update({"class": "timepoint"})
        return tratts


class VehicleTable(Table):
    routecol = LinkCol("Route", "routeInfo",
                       url_kwargs=dict(rquery="rname"),
                       attr="route",
                       th_html_attrs={"title": "Route"})
    vehcol = LinkCol("Vehicle ID", "routeInfo",
                     url_kwargs=dict(rquery="rname", trip="trip_id"),
                     attr="vehicle",
                     th_html_attrs={"title": "Assigned vehicle id"},
                     td_html_attrs={"class": "centrecol"})
    direction = Col("Direction",
                    th_html_attrs={"title": "Direction of trip"},
                    td_html_attrs={"class": "centrecol"})
    departed = Col("Departed",
                    th_html_attrs={"title":
                                   "Time of departure from initial stop"},
                    td_html_attrs={"class": "centrecol"})
    status = Col("Status",
                    th_html_attrs={"title":
                                   "Status"})

    table_id = "vehicletable"
    classes = ["cleantable"]


app = Flask(__name__)
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

app.apscheduler.add_job(func=updateFeedInfo, trigger="cron", args=[True],
                        minute='11', hour='3', id="ufeedinfo")
app.apscheduler.add_job(func=updateAlerts, trigger="cron", args=[True],
                        minute='*/5', id="ualerts")
app.apscheduler.add_job(func=updatePositions, trigger="cron", minute="*",
                        id="upos")
app.apscheduler.add_job(func=updateTripUpdates, trigger="cron", minute="*/2",
                        id="utrip")

def stopExtract(code, name):
    z = re.match("{} - (.*)".format(code), name)
    return z.groups()[0]

def minCompare(comp, origin):
    return 0 if comp <= origin else (comp - origin).seconds // 60

def statusString(service):
    status = service["status"]
    if status in depStatus:
        aimedD = service["departure"]["aimed"]
        estD = service["departure"]["expected"]
        if status == "delayed" and aimedD is not None and estD is not None:
            return "{}m late".format(minCompare(isoparse(estD),
                                                isoparse(aimedD)))
        elif status == "early" and aimedD is not None and estD is not None:
            return "{}m early".format(minCompare(isoparse(aimedD),
                                                isoparse(estD)))
        else:
            return depStatus[status]
    else:
        return status


def validDates(dates):
    if dates is None or len(dates) == 0:
        return None

    mind = min(dates)
    maxd = max(dates)
    drange = [mind + dt.timedelta(days = x) for x in range(0, (maxd -
                                                               mind).days + 1)]
    t_days = [len([y for y in drange if int(y.strftime("%w")) == x]) for x in range(0, 7)]
    r_days = [len([y for y in dates  if int(y.strftime("%w")) == x]) for x in range(0, 7)]
    typical = [x for x in range(0, 7) if r_days[x] >= t_days[x] / 2]
    missing = [x for x in range(0, 7) if r_days[x] < t_days[x] and x in
               typical]
    extra = [x for x in range(0, 7) if r_days[x] > 0 and x not in typical]
    missing_dates = [date for date in drange if int(date.strftime("%w")) in
                     missing and date not in dates]
    extra_dates = [date for date in dates if int(date.strftime("%w")) in extra]
    typical_7 = [((x - 1) % 7) + 1 for x in typical]
    typical_str = "-".join([dayShort[x] for x in dayShort if x in typical_7])
    return {
        "min": mind,
        "max": maxd,
        "first_d": typical_7[0] if len(typical_7) > 0 else 0,
        "str": typical_str,
        "extra": ", ".join([d.strftime("%a %-d %b") for d in extra_dates]) if len(extra_dates) > 0 else None,
        "missing": ", ".join([d.strftime("%a %-d %b") for d in
                              missing_dates]) if len(missing_dates) > 0 else None
    }


@app.route("/robots.txt")
def static_page():
    return send_from_directory(app.static_folder, request.path[1:])


@app.route("/")
def rti():
    return render_template("main.html", routes=sortedRouteCodes(),
                           footer=footerData())


@app.route("/stop/")
def Search():
    ra = request.args
    if "stopnum" in ra:
        return redirect("/stop/{}/".format(ra["stopnum"].strip()), 302, None)
    else:
        return redirect("/", 302, None)


@app.route("/stop/<string:stop>/")
def timetable(stop):
    req = requests.get(depurl, params={"stop_id": stop}, headers=headers,
                       timeout=20)
    if req.status_code != 200:
        if stop in stopids:
            parent = stopinfo[stopids[stop]]["parent_station"]
            if parent != "":
                return redirect("/stop/{}/".format(parent.strip()), 302, None)
        return render_template("nostop.html",
                               error=req.status_code,
                               footer=footerData())
    stopname = "Unknown Stop"
    if stop in stopids:
        stopname = stopinfo[stopids[stop]]["stop_name"]
    rv = req.json()
    lastup = dt.datetime.now(patz)
    if "departures" in rv:
        ttdat = [{"route": s["service_id"], "rname": s["service_id"],
                  "dest": s["destination"]["name"],
                  "trip_id": s["trip_id"] if "trip_id" in s and s["trip_id"] !=
                      False and s["trip_id"] != "false" else None,
                  "sched":
                      isoparse(s["departure"]["aimed"]).strftime("%H:%M"),
                  "est": "" if s["departure"]["expected"] is None else
                      "{} mins".format(
                          minCompare(isoparse(s["departure"]["expected"]),
                                                  lastup)),
                  "status": statusString(s)}
                 for s in rv["departures"]]
        tTable = TimeTable(ttdat)
        seen_routes = [t["route"] for t in ttdat]
        seen_trips = [t["trip_id"] for t in ttdat]
        rel_alerts = [a for a in alertlist if
                      stop in a["stops"] or
                      any([x in a["routes"] for x in seen_routes]) or
                      any([x in a["trips"] for x in seen_trips])]
    else:
        if stop in stopids:
            print(stop)
            parent = stopinfo[stopids[stop]]["parent_stop"]
            print(parent)
            if parent != "":
                return redirect("/stop/{}/".format(parent.strip()), 302, None)
        ttdat = []
        rel_alerts = []
    return render_template("stop.html", stopnumber=stop,
                           stopname=stopname,
                           zone=rv["farezone"] if "farezone" in rv else "?",
                           lup=lastup.strftime("%H:%M:%S, %A %B %-d"),
                           table=tTable if len(ttdat) > 0 else None,
                           alerts=rel_alerts,
                           footer=footerData())


@app.route("/search/")
def stopsearch():
    query = request.args["q"].strip() if "q" in request.args else ""
    if query == "":
        return render_template("badsearch.html",
                               lup=stoplastupdate.strftime("%A %B %-d"),
                               footer=footerData())
    qlower = query.lower()
    ranknames = [(name, fuzz.token_set_ratio(name.lower(), qlower)) for name in
               list(stopnames.keys())]
    toprank = [tup for tup in ranknames if tup[1] > 40]
    if len(toprank) == 0:
        return render_template("badsearch.html", footer=footerData())
    toprank.sort(reverse=True, key=lambda a: a[1])
    stdat = [{"code": stopnames[name], "sms": stopinfo[stopids[stopnames[name]]]["parent_station"] if
              stopinfo[stopids[stopnames[name]]]["parent_station"] != "" else stopnames[name], "stop":
              name, "zone":
              stopinfo[stopids[stopnames[name]]]["zone_id"]}
             for name, ratio in toprank[:20]]
    sTable = StopTable(stdat)
    return render_template("search.html", searchstring=query,
                           numres=len(stdat),
                           lup=stoplastupdate.strftime("%A %B %-d"),
                           table=sTable if len(stdat) > 0 else "",
                           footer=footerData())


@app.route("/route/")
def ttSearch():
    ra = request.args
    if "trip" in ra:
        thisroute = [x["route_id"] for x in triplist if x["trip_id"] ==
                     ra["trip"]]
        if len(thisroute) > 0:
            return redirect("/route/{}/?trip={}".format(servroute[thisroute[0]],
                                                        ra["trip"]))
    if "r" in ra:
        return redirect("/route/{}/".format(ra["r"].strip()), 302, None)
    else:
        return redirect("/", 303, None)


@app.route("/route/<string:rquery>/")
def routeInfo(rquery):
    if rquery == "" or rquery not in routelist:
        return render_template("badroute.html", error="No such route",
                               lup=routeslastupdate.strftime("%A %B %-d"),
                               routes=sortedRouteCodes(), footer=footerData())
    routeinfo = routelist[rquery]
    ra = request.args
    rtrip = ("trip" in ra and ra["trip"] != "" and ra["trip"] != "none")
    ttrip = None
    route_trips = [x["trip_id"] for x in triplist if x["route_id"] ==
                   routeinfo["route_id"]]
    route_spats = set([trip_sid[t] for t in route_trips])
    slist = []
    thistripinfo = []
    if rtrip:
        ttrip = ra["trip"]
        thistripinfo = [x for x in triplist if x["route_id"] ==
                     routeinfo["route_id"] and x["trip_id"] == ttrip]
        rtrip = len(thistripinfo) > 0 and ttrip in trip_stop_times
    if rtrip:
        slist = [{"stop_id": s["id"],
                  "time": s["time"],
                  "tp": s["tp"],
                  "inf": stopinfo[s["sind"]] if s["sind"] is
                  not None else None} for s in
                 trip_stop_times[ttrip]]
    route_code = routeinfo["route_short_name"]
    route_name = routeinfo["route_long_name"]
    inds = []
    for spat in enumerate(route_spats):
        inds.extend([(sp["sind"], spat[0]) for sp in
                     stop_patterns[spat[1]]])
    if len(inds) == 0:
        return render_template("badroute.html", error="No stops in route",
                               routes=sortedRouteCodes(),
                               lup=routeslastupdate.strftime("%A %B %-d"),
                               footer=footerData())
    rmv = []
    for i in range(0, len(inds) - 1):
        if inds[i][0] == inds[i + 1][0]:
            rmv.append(i)

    in2 = [ev[1] for ev in enumerate(inds) if ev[0] not in rmv]

    rv = [stopinfo[i[0]] if i[0] is not None else None for i in in2]
    for r in enumerate(rv):
        if rv[r[0]] is not None:
            rv[r[0]]["pattern"] = in2[r[0]][1] + 1
    rv = [r for r in rv if r is not None]
    if rtrip:
        dates = caldates.get(trip_serv.get(ttrip))
        vdates = validDates(dates)
        datetable = None
        #if dates is not None and len(dates) > 0:
        #    datetable = DateTable([{"day": date.strftime("%A"), "date":
        #                            date.strftime("%-d %B %Y")} for date in
        #                           dates])
        tripstops = [s.get("inf") for s in slist if
                     s.get("inf") is not None]
        vehdata = None
        a_vid = None
        vehposdat = trip_positions.get(ttrip)
        if vehposdat is not None and len(tripstops) > 0:
            can_stops = [{"id": stop["stop_id"], "name": stop["stop_name"],
                          "dlat": vehposdat["lat"] - stop["stop_lat"],
                          "dlon": vehposdat["lon"] - stop["stop_lon"],
                          "dist2": planeDistance2(stop["stop_lat"],
                                                  stop["stop_lon"],
                                                  vehposdat["lat"],
                                                  vehposdat["lon"])}
                          for stop in tripstops]
            can_stops.sort(key=lambda x: x["dist2"])
            c_stop = can_stops[0]
            a_vid = vehposdat["vehicle_id"]
            vehdata = {
                "dtime": (dt.datetime.now(patz) -
                          vehposdat["timestamp"]).seconds,
                "ob_time": vehposdat["timestamp"],
                "s_dist": prettyDistance(sqrt(c_stop["dist2"])),
                "s_head": directions.get(heading(c_stop["dlat"],
                                                 c_stop["dlon"])),
                "s_id": c_stop["id"],
                "s_name": c_stop["name"],
                "bearing": directions.get(headingdeg(vehposdat["bearing"]))
            }
        vehtripdat = trip_updates.get(ttrip)
        veh_tup = None
        if vehtripdat is not None:
            if a_vid is None:
                a_vid = vehtripdat.get("vid")
            delay = vehtripdat["delay"]
            veh_tup = {
                "sr": vehtripdat["sr"],
                "ob_time": vehtripdat["ts"],
                "delay": "on time" if abs(delay) < 30 else ("{} minute{} "
                "{}").format(round(abs(delay) / 60),
                             "s" if round(abs(delay) / 60) != 1 else "",
                             "early" if delay < 0 else "late")
            }
        direction = "Outbound" if trip_dir[ttrip] == "0" else "Inbound"
        rstopsdat = [{"code": stop["stop_id"],
                      "sms": stop["inf"]["parent_station"] if
                          stop["inf"] is not None and
                          stop["inf"]["parent_station"] != "" else
                          stop["stop_id"],
                      "stop": stop["inf"]["stop_name"] if stop["inf"] is not
                          None else "",
                      "zone": stop["inf"]["zone_id"] if stop["inf"] is not None
                          else "",
                      "tp": stop["tp"],
                      "sched": stop["time"],
                      "nearest": stop["stop_id"] == vehdata["s_id"] if vehdata
                     is not None else False} for stop in slist]
        rTable = StopTimeTable(rstopsdat)
        rel_alerts = [alert for alert in alertlist if ttrip in
                      alert["trips"] or route_code in alert["routes"]]
        return render_template("trip.html", code=route_code, name=route_name,
                               table=rTable if len(rstopsdat) > 0 else "",
                               direction=direction,
                               routes=sortedRouteCodes(),
                               footer=footerData(), alerts = rel_alerts,
                               valid_dates=vdates, datetable=datetable,
                               v_pos=vehdata, v_upd=veh_tup, vehicle=a_vid)
    else:
        rstopsdat = [{"code": stop["stop_id"],
                      "sms": stop["parent_station"] if
                      stop["parent_station"] != "" else stop["stop_id"],
                      "stop": stop["stop_name"],
                      "zone": stop["zone_id"]} for stop in rv]
        rtrips = routetrips.get(routeinfo["route_id"])
        triptab = None
        if rtrips is not None and len(rtrips) > 0:
            tripsdat = [{"rname": rquery,
                         "trip_id": t,
                         "seq": trip_seq[t],
                         "direction": "Outbound" if (trip_dir[t] ==
                                                     "0") else "Inbound",
                         "vehicle": trip_positions[t]["vehicle_id"],
                         "departed":
                         trip_positions[t]["start_time"]}
                        for t in rtrips if t in trip_positions]
            if len(tripsdat) > 0:
                tripsdat.sort(key=lambda x: x["direction"])
                tripsdat.sort(key=lambda x: int(x["seq"]) if x["seq"].isnumeric()
                              else 0)
                triptab = TripTable(tripsdat)
        all_stops = [s["code"] for s in rstopsdat]
        rel_alerts = [alert for alert in alertlist if route_code in
                      alert["routes"] or any([x in alert["stops"] for x in
                                              all_stops])]
        rTable = StopTable(rstopsdat)
        return render_template("route.html", code=route_code, name=route_name,
                               table=rTable if len(rstopsdat) > 0 else "",
                               lup=routeslastupdate.strftime("%A %B %-d"),
                               trips=triptab,
                               routes=sortedRouteCodes(),
                               footer=footerData(), alerts = rel_alerts)


@app.route("/timetable/")
def ttabse():
    ra = request.args
    if "r" in ra:
        return redirect(url_for("routeTimetable", rquery=ra["r"],
                                date = ra.get("date"), stops = ra.get("stops")), 302, None)
    else:
        return redirect("/", 303, None)


@app.route("/timetable/<string:rquery>/")
def routeTimetable(rquery):
    if rquery == "" or rquery not in routelist:
        return redirect("/", 303, None)
    routeinfo = routelist[rquery]
    route_name = routeinfo["route_long_name"]
    ra = request.args
    tponly = "stops" not in ra or ra["stops"] != "all"
    todaydate = dt.datetime.now(patz).date()
    try:
        ttdate = dt.datetime.strptime(ra["date"], "%Y-%m-%d").date()
    except:
        ttdate = todaydate
    route_trips = [x for x in triplist if x["route_id"] ==
                   routeinfo["route_id"]]
    day_trips = [trip for trip in route_trips if
                 caldates.get(trip_serv.get(trip["trip_id"])) is not None and
                 ttdate in [cdate.date() for cdate in
                            caldates.get(trip_serv.get(trip["trip_id"]))]]
    alldates = [caldates.get(trip_serv.get(trip["trip_id"])) for trip in
                route_trips if trip_serv.get(trip["trip_id"]) in caldates]
    alldates = [cdate.date() for tripdays in alldates for cdate in tripdays]
    alldates = list(set(alldates))
    alldates.sort()
    prevdates = [tripday for tripday in alldates if tripday < ttdate]
    ldate = None if len(prevdates) == 0 else max(prevdates)
    folldates = [tripday for tripday in alldates if tripday > ttdate]
    ndate = None if len(folldates) == 0 else min(folldates)
    # Outbound trips
    out_trips = [trip for trip in day_trips if trip.get("direction_id") != "1"]
    out_table = tripTimeTable(out_trips, rquery, "outbound-timetable", tponly)
    # Inbound trips
    in_trips = [trip for trip in day_trips if trip.get("direction_id") == "1"]
    in_table = tripTimeTable(in_trips, rquery, "inbound-timetable", tponly)
    return render_template("timetable.html", code=rquery,
                           ttdate=ttdate,
                           todaydate=todaydate,
                           name=route_name,
                           outbound=out_table,
                           inbound=in_table,
                           ldate=ldate,
                           ndate=ndate,
                           tponly=tponly,
                           routes=sortedRouteCodes(),
                           footer=footerData())


@app.route("/stop/<string:stop>/nearby/")
def nearbyStops(stop):
    if stop == "" or stop not in stopids:
        return render_template("badnearby.html",
                               error = "Stop not found",
                               lup=stoplastupdate.strftime("%A %B %-d"),
                               footer=footerData())
    thisstop = stopinfo[stopids[stop]]
    stopDistances = [{"id": x["stop_id"],
                      "parent": x["parent_station"],
                      "name": x["stop_name"],
                      "zone": x["zone_id"],
                      "dlat": x["stop_lat"] - thisstop["stop_lat"],
                      "dlon": x["stop_lon"] - thisstop["stop_lon"],
                      "dist2": planeDistance2(thisstop["stop_lat"],
                                              thisstop["stop_lon"],
                                              x["stop_lat"],
                                              x["stop_lon"])}
                     for x in stopinfo if x["stop_id"] != stop]
    stopDistances = [x for x in stopDistances if x["dist2"] >= 1]
    if len(stopDistances) == 0:
        return render_template("badnearby.html",
                               error = "No nearby stops found",
                               lup=stoplastupdate.strftime("%A %B %-d"),
                               footer=footerData())
    stopDistances.sort(key=lambda x: x["dist2"])
    nstopsDat = [{"code": x["id"],
                  "sms": x["parent"] if x["parent"] != "" else
                  x["id"],
                  "stop": x["name"],
                  "zone": x["zone"],
                  "distance": "{}m {}".format(
                      prettyDistance(sqrt(x["dist2"])),
                      heading(x["dlat"], x["dlon"]))} for x in
                 stopDistances[:20]]
    nTable = LocationTable(nstopsDat)
    return render_template("nearby.html", code=stop,
                           name=thisstop["stop_name"],
                           zone=thisstop["zone_id"],
                           lup=stoplastupdate.strftime("%A %B %-d"),
                           table=nTable, footer=footerData())

@app.route("/alerts/")
def showAllAlerts():
    return render_template("alerts.html", alerts=alertlist,
                           lup=alertslastupdate,
                           footer=footerData())


@app.route("/vehicles/")
def showAllVehicles():
    vehdat = [{"rname": servroute.get(str(trip_positions[t]["route_id"])) if 
               str(trip_positions[t]["route_id"]) in servroute else "",
                 "route": servroute.get(str(trip_positions[t]["route_id"])) if 
               str(trip_positions[t]["route_id"]) in servroute else "",
                 "trip_id": t,
                 "direction": "Outbound" if (trip_positions[t]["direction"] ==
                                             0) else "Inbound",
                 "vehicle": trip_positions[t]["vehicle_id"],
                 "departed": trip_positions[t]["start_time"],
                 "delay": 0 if t not in trip_updates else
                   trip_updates[t]["delay"],
                 "status": "" if t not in trip_updates else (
                     "On time" if abs(trip_updates[t]["delay"]) <= 30 else
                     "{}m {}".format(round(abs(trip_updates[t]["delay"]) / 60),
                                    "late" if trip_updates[t]["delay"] > 0
                                     else "early")
                 )}
                for t in trip_positions]
    fdat = footerData()
    vehdat.sort(key=lambda x: x["route"])
    vehdat.sort(key=lambda x: x["departed"])
    vehtab = None if fdat["vehicles"] == 0 else VehicleTable(vehdat)
    stats = None
    if fdat["vehicles"] > 0 and len(trip_updates) > 0:
        delvals = [t["delay"] for t in vehdat]
        val_n = len(delvals)
        delvals.sort()
        med = (delvals[val_n // 2] if val_n % 2 == 1
               else (delvals[(val_n // 2) - 1] + delvals[val_n // 2])/2)
        del5 = sum([1 for x in delvals if x > 60*3])
        ear5 = sum([1 for x in delvals if x < -60*3])
        stats = {"n": val_n,
                 "early": ear5,
                 "earlyp": round((ear5/val_n)*100),
                 "late": del5,
                 "latep": round((del5/val_n)*100),
                 "median_abs": abs(med),
                 "median_status": "early" if med < 0 else "late",
                 "median_text": ("on time" if abs(med) <= 30
                                 else "{} minute{} {}".format(
                                     abs(round(med / 60)),
                                     "" if abs(round(med/60)) == 1 else "s",
                                     "early" if med < 0 else "late"))}


    return render_template("vehicles.html", table=vehtab,
                           lup=positionlastupdate, stats=stats,
                           footer=fdat)



if __name__ == "__main__":
    app.run()


