from flask import Flask, render_template, request, redirect
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
zipurl = "https://static.opendata.metlink.org.nz/v1/gtfs/full.zip"

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
stoplastupdate = dt.datetime.now(patz) - dt.timedelta(days=14)
routeslastupdate = dt.datetime.now(patz) - dt.timedelta(days=14)
alertslastupdate = dt.datetime.now(patz) - dt.timedelta(seconds=60*20)

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
    nowtime = dt.datetime.now(patz)
    print("Loading zip of metadata at {}".format(nowtime.strftime("%c")))
    if not exists("GTFS_full.zip"):
        return False
    with zipfile.ZipFile("GTFS_full.zip") as z:
        znames = z.namelist()
        if not all(needed_file in znames for needed_file in ["feed_info.txt",
                                                             "trips.txt",
                                                             "routes.txt",
                                                             "stops.txt"]):
            return False

        with textwrap(z.open("trips.txt"), encoding="utf-8") as tripfile:
            triplist = []
            triprows = csv.DictReader(tripfile)
            for row in triprows:
                triplist.append(row)
            alltrips = [trip["trip_id"] for trip in triplist]
        if len(triplist) == 0:
            return False
        print("done trips")

        with textwrap(z.open("stops.txt"), encoding="utf-8") as stopfile:
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

        with textwrap(z.open("routes.txt"), encoding="utf-8") as routefile:
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

        with textwrap(z.open("feed_info.txt"), encoding="utf-8") as feedfile:
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
    if zipinfo["feed_start_date"] < feedinfo["feed_start_date"]:
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


updateFeedInfo(True)
updateAlerts(True)

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
    zone = Col("Zone", td_html_attrs = {"class": "zonecol"})
    table_id = "stoptable"
    classes = ["cleantable"]


class StopTimeTable(Table):
    code = LinkCol("Code", "timetable", url_kwargs=dict(stop="sms"),
                   attr='code')
    stop = Col("Stop")
    sched = Col("Sched")
    zone = Col("Zone", td_html_attrs = {"class": "zonecol"})
    table_id = "stoptimetable"
    classes = ["cleantable"]


class LocationTable(Table):
    code = LinkCol("Code", "timetable", url_kwargs=dict(stop="sms"),
                   attr='code')
    stop = Col("Stop")
    zone = Col("Zone", td_html_attrs = {"class": "zonecol"})
    distance = Col("Distance")
    table_id = "locationtable"
    classes = ["cleantable"]


app = Flask(__name__)
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

app.apscheduler.add_job(func=updateFeedInfo, trigger="cron", args=[True],
                        minute='11', hour='3', id="ufeedinfo")
app.apscheduler.add_job(func=updateAlerts, trigger="cron", args=[True],
                        minute='*/5', id="ualerts")

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
        # return "Error {}".format(req.status_code)
        return render_template("nostop.html", error=req.status_code, nalerts =
                           len(alertlist))
    # return req.json()
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
    # return sTable.__html__()
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
    rtrip = ("trip" in ra and ra["trip"] != "" and ra["trip"] != "none")
    slist = []
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
        # return "Error {}".format(req.status_code)
        return render_template("badroute.html", error=req.status_code,
                               lup=routeslastupdate.strftime("%A %B %-d"),
                               routes=sortedRouteCodes(), nalerts =
                               len(alertlist))
    rv = req.json()
    if len(rv) == 0:
        return render_template("badroute.html", error="No stops in route",
                               routes=sortedRouteCodes(), nalerts =
                               len(alertlist))
    if rtrip:
        direction = "Outbound" if re.match("^[^_]*__([01])", ra["trip"]).groups()[0] == "0" else "Inbound"
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
                               nalerts = len(alertlist), alerts = rel_alerts)
    else:
        rstopsdat = [{"code": stop["stop_id"],
                      "sms": stop["parent_station"] if
                      stop["parent_station"] != "" else stop["stop_id"],
                      "stop": stop["stop_name"],
                      "zone": stop["zone_id"]} for stop in rv]
        all_stops = [s["code"] for s in rstopsdat]
        rel_alerts = [alert for alert in alertlist if route_code in
                      alert["routes"] or any([x in alert["stops"] for x in
                                              all_stops])]
        rTable = StopTable(rstopsdat)
        return render_template("route.html", code=route_code, name=route_name,
                               table=rTable if len(rstopsdat) > 0 else "",
                               lup=routeslastupdate.strftime("%A %B %-d"),
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
