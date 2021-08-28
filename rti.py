from flask import Flask, render_template, request, redirect, send_from_directory
from flask_apscheduler import APScheduler
from flask_table import Table, Col, LinkCol
from dateutil.parser import isoparse
from dateutil.tz import gettz
from math import cos, atan, pi, sqrt, log10, floor
from os.path import exists
from io import TextIOWrapper as textwrap
import zipfile
import datetime as dt
import time
import requests
import re
import csv
from urllib.parse import quote
from fuzzywuzzy import fuzz

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
trip_positions = {}
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
            #return remd.replace("_", " ") + ext
            remd = remd.replace("_", " ")
            return remd.replace("  ", "_")
        else:
            return remd[:int(len(remd) / 2 - 1)].replace("__", "_") + ext
    return None


def downloadZipDataset():
    print("Downloading zip of GTFS metadata")
    req = requests.get(zipurl)
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
                                                             "calendar_dates.txt",
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
            sptrows = csv.DictReader(spfile)
            for row in sptrows:
                trip_serv[row["trip_id"]] = servfromtrip(row["trip_id"],
                                                         agencies.keys())
                trip_seq[row["trip_id"]] = row["trip_sequence"]
        if len(trip_serv) == 0:
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
        req = requests.get(feedinfourl, headers=headers)
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
        req = requests.get(alertsurl, headers=headers)
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
    req = requests.get(positionurl, headers=headers)
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
        keepovers = {t: trip_positions[t] for t in trip_positions if t not in
                     tpdict and (datstamp -
                                 trip_positions[t]["timestamp"]).seconds <
                     60*5}
        if len(keepovers) > 0:
            tpdict.update(keepovers)
        positionlastupdate = datstamp
        trip_positions = tpdict


updateFeedInfo(True)
updateAlerts(True)
updatePositions()

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


def prettyDistance(dist, fig=1):
    return floor(round(dist, fig - 1 -floor(log10(dist))))


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
    sched = Col("Sched")
    zone = Col("Zone", td_html_attrs = {"class": "centrecol"})
    table_id = "stoptimetable"
    classes = ["cleantable"]

    def get_tr_attrs(self, item):
        return {'id': "stop-{}".format(item["code"])}


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
    return render_template("main.html", routes=sortedRouteCodes(), nalerts =
                           len(alertlist))


@app.route("/stop/")
def Search():
    ra = request.args
    if "stopnum" in ra:
        return redirect("/stop/{}/".format(ra["stopnum"].strip()), 302, None)
    else:
        return redirect("/", 302, None)


@app.route("/stop/<string:stop>/")
def timetable(stop):
    req = requests.get(depurl, params={"stop_id": stop}, headers=headers)
    if req.status_code != 200:
        if stop in stopids:
            parent = stopinfo[stopids[stop]]["parent_station"]
            if parent != "":
                return redirect("/stop/{}/".format(parent.strip()), 302, None)
        return render_template("nostop.html", error=req.status_code, nalerts =
                           len(alertlist))
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
                           alerts=rel_alerts, nalerts = len(alertlist))


@app.route("/search/")
def stopsearch():
    query = request.args["q"].strip() if "q" in request.args else ""
    if query == "":
        return render_template("badsearch.html",
                               lup=stoplastupdate.strftime("%A %B %-d"), nalerts =
                               len(alertlist))
    qlower = query.lower()
    ranknames = [(name, fuzz.token_set_ratio(name.lower(), qlower)) for name in
               list(stopnames.keys())]
    toprank = [tup for tup in ranknames if tup[1] > 40]
    if len(toprank) == 0:
        return render_template("badsearch.html", nalerts =
                           len(alertlist))
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
                           table=sTable if len(stdat) > 0 else "", nalerts =
                           len(alertlist))


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
                               routes=sortedRouteCodes(), nalerts =
                               len(alertlist))
    routeinfo = routelist[rquery]
    ra = request.args
    rtrip = ("trip" in ra and ra["trip"] != "" and ra["trip"] != "none") slist = []
    thistripinfo = []
    if rtrip:
        thistripinfo = [x for x in triplist if x["route_id"] ==
                     routeinfo["route_id"] and x["trip_id"] == ra["trip"]]
        rtrip = len(thistripinfo) > 0
    if rtrip:
        req = requests.get(stoptimesurl, params={"trip_id": ra["trip"]},
                           headers=headers)
        if req.status_code != 200:
            rtrip = False
        else:
            slist = req.json()
    route_code = routeinfo["route_short_name"]
    route_name = routeinfo["route_long_name"]
    req = requests.get(stoplisturl, params={"route_id": routeinfo["route_id"]}, headers=headers)
    if req.status_code != 200:
        return render_template("badroute.html", error=req.status_code,
                               lup=routeslastupdate.strftime("%A %B %-d"),
                               routes=sortedRouteCodes(), nalerts =
                               len(alertlist))
    rv = req.json()
    if len(rv) == 0:
        return render_template("badroute.html", error="No stops in route",
                               routes=sortedRouteCodes(),
                               lup=routeslastupdate.strftime("%A %B %-d"),
                               nalerts=len(alertlist))
    if rtrip:
        dates = caldates.get(trip_serv.get(ra["trip"]))
        vdates = validDates(dates)
        datetable = None
        #if dates is not None and len(dates) > 0:
        #    datetable = DateTable([{"day": date.strftime("%A"), "date":
        #                            date.strftime("%-d %B %Y")} for date in
        #                           dates])
        tripstops = [stopinfo[stopids[stop["stop_id"]]] for
                     stop in slist if stop["stop_id"] in stopids]
        vehdata = None
        vehtripdat = trip_positions.get(ra["trip"])
        if vehtripdat is not None and len(tripstops) > 0:
            can_stops = [{"id": stop["stop_id"], "name": stop["stop_name"],
                          "dlat": vehtripdat["lat"] - stop["stop_lat"],
                          "dlon": vehtripdat["lon"] - stop["stop_lon"],
                          "dist2": planeDistance2(stop["stop_lat"],
                                                  stop["stop_lon"],
                                                  vehtripdat["lat"],
                                                  vehtripdat["lon"])}
                          for stop in tripstops]
            can_stops.sort(key=lambda x: x["dist2"])
            c_stop = can_stops[0]
            vehdata = {
                "id": vehtripdat["vehicle_id"],
                "dtime": (dt.datetime.now(patz) -
                          vehtripdat["timestamp"]).seconds,
                "ob_time": vehtripdat["timestamp"],
                "s_dist": prettyDistance(sqrt(c_stop["dist2"])),
                "s_head": directions.get(heading(c_stop["dlat"],
                                                 c_stop["dlon"])),
                "s_id": c_stop["id"],
                "s_name": c_stop["name"],
                "bearing": directions.get(headingdeg(vehtripdat["bearing"]))
            }
        direction = "Outbound" if trip_dir[ra["trip"]] == "0" else "Inbound"
        rstopsdat = [{"code": stop["stop_id"],
                      "sms": stopinfo[stopids[stop["stop_id"]]]["parent_station"] if
                          stop["stop_id"] in stopids and
                          stopinfo[stopids[stop["stop_id"]]]["parent_station"] !=
                          "" else stop["stop_id"],
                      "stop": stopinfo[stopids[stop["stop_id"]]]["stop_name"]
                          if stop["stop_id"] in stopids else "",
                      "zone": stopinfo[stopids[stop["stop_id"]]]["zone_id"]
                          if stop["stop_id"] in stopids else "",
                      "sched": stop["arrival_time"]} for stop in slist]
        rTable = StopTimeTable(rstopsdat)
        rel_alerts = [alert for alert in alertlist if ra["trip"] in
                      alert["trips"] or route_code in alert["routes"]]
        return render_template("trip.html", code=route_code, name=route_name,
                               table=rTable if len(rstopsdat) > 0 else "",
                               direction=direction,
                               routes=sortedRouteCodes(),
                               nalerts = len(alertlist), alerts = rel_alerts,
                               valid_dates=vdates, datetable=datetable,
                               vehicle=vehdata)
    else:
        rstopsdat = [{"code": stop["stop_id"],
                      "sms": stop["parent_station"] if
                      stop["parent_station"] != "" else stop["stop_id"],
                      "stop": stop["stop_name"],
                      "zone": stop["zone_id"]} for stop in rv]
        rtrips = routetrips.get(routeinfo["route_id"])
        triptab = None
        if rtrips is not None and len(rtrips) > 0:
            tripdays = {t: validDates(caldates.get(trip_serv.get(t))) for t in
                        rtrips}
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
                tripsdat.sort(key=lambda x: int(x["seq"]) if x["seq"].isnumeric()
                              else 0)
                tripsdat.sort(key=lambda x: x["direction"])
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
                               nalerts = len(alertlist), alerts = rel_alerts)

@app.route("/stop/<string:stop>/nearby/")
def nearbyStops(stop):
    if stop == "" or stop not in stopids:
        return render_template("badnearby.html",
                               error = "Stop not found",
                               lup=stoplastupdate.strftime("%A %B %-d"), nalerts =
                           len(alertlist))
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
                               lup=stoplastupdate.strftime("%A %B %-d"), nalerts =
                               len(alertlist))
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
                           table=nTable, nalerts =
                           len(alertlist))

@app.route("/alerts/")
def showAllAlerts():
    return render_template("alerts.html", alerts = alertlist, lup =
                           alertslastupdate.strftime("%H:%M, %A %B %-d"), nalerts =
                           len(alertlist))


if __name__ == "__main__":
    app.run()
