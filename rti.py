from flask import Flask, render_template, request, redirect
from flask_table import Table, Col, LinkCol
from dateutil.parser import isoparse
from dateutil.tz import gettz
import datetime as dt
import requests
import re
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

patz = gettz("Pacific/Auckland")

depurl = "https://api.opendata.metlink.org.nz/v1/stop-predictions"
stoplisturl = "https://api.opendata.metlink.org.nz/v1/gtfs/stops"
routelisturl = "https://api.opendata.metlink.org.nz/v1/gtfs/routes"

stopinfo = []
stopids = {}
stopnames = {}
routelist = {}
stoplastupdate = dt.datetime.now(patz) - dt.timedelta(days=14)
routeslastupdate = dt.datetime.now(patz) - dt.timedelta(days=14)

def updateStopInfo(force=False):
    global stopinfo
    global stopids
    global stopnames
    global stoplastupdate
    nowtime = dt.datetime.now(patz)
    if force or (nowtime - stoplastupdate).days >= 7:
        req = requests.get(stoplisturl, headers=headers)
        if req.status_code != 200:
            return
        stopinfo = req.json()
        stopids = {x["stop_id"]: ind for ind, x in enumerate(stopinfo)}
        stopnames = {x["stop_name"]: x["stop_id"] for x in stopinfo}
        stoplastupdate = nowtime
        print("Updated stop metadata at {}.".format(nowtime.strftime("%c")))

def updateRouteInfo(force=False):
    global routelist
    global routeslastupdate
    nowtime = dt.datetime.now(patz)
    if force or (nowtime - routeslastupdate).days >= 7:
        req = requests.get(routelisturl, headers=headers)
        if req.status_code != 200:
            return
        routeinfo = req.json()
        routelist = {x["route_short_name"]: x for x in routeinfo}
        routeslastupdate = nowtime
        print("Updated route metadata at {}.".format(nowtime.strftime("%c")))


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
    updateRouteInfo()
    rcodes = list(routelist.keys())
    if rcodes is None or len(rcodes) == 0:
        return None
    rcodes.sort(key=routeCodeKey)
    return rcodes


class TimeTable(Table):
    routeCol = LinkCol("Route", "routeInfo", th_html_attrs={"title": "Route"},
                    url_kwargs=dict(rquery="rname"), attr='route')
    dest = Col("Dest", th_html_attrs={"title": "Destination"})
    sched = Col("Sched", th_html_attrs={"title": "Scheduled departure time"})
    status = Col("Status", th_html_attrs={"title": "Status"})
    est = Col("Est", th_html_attrs={"title": "Estimated time until departure"})
    table_id = "stoptimetable"


class StopTable(Table):
    code = LinkCol("Code", "timetable", url_kwargs=dict(stop="sms"),
                   attr='code')
    stop = Col("Stop")
    zone = Col("Zone", td_html_attrs = {"class": "zonecol"})
    table_id = "stoptable"


updateStopInfo(True)
updateRouteInfo(True)

app = Flask(__name__)

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
    return render_template("main.html", routes=sortedRouteCodes())


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
        return render_template("nostop.html", error=req.status_code)
    # return req.json()
    stopname = "Unknown Stop"
    updateStopInfo()
    if stop in stopids:
        stopname = stopinfo[stopids[stop]]["stop_name"]
    rv = req.json()
    lastup = dt.datetime.now(patz)
    if "departures" in rv:
        ttdat = [{"route": s["service_id"], "rname": s["service_id"],
                  "dest": s["destination"]["name"],
                  "sched":
                  isoparse(s["departure"]["aimed"]).strftime("%H:%M"),
                  "est": "" if s["departure"]["expected"] is None else
                      "{} mins".format(
                          minCompare(isoparse(s["departure"]["expected"]),
                                                  lastup)),
                  "status": statusString(s)}
                 for s in rv["departures"]]
        tTable = TimeTable(ttdat)
    else:
        ttdat = []
    return render_template("stop.html", stopnumber=stop,
                           stopname=stopname,
                           zone=rv["farezone"] if "farezone" in rv else "?",
                           lup=lastup.strftime("%H:%M:%S, %A %B %-d"),
                           table=tTable if len(ttdat) > 0 else None,
                           notices=[n["LineNote"] for n in rv["Notices"]] if "Notices" in rv else None)


@app.route("/search/")
def stopsearch():
    updateStopInfo()
    query = request.args["q"].strip() if "q" in request.args else ""
    if query == "":
        return render_template("badsearch.html")
    qlower = query.lower()
    ranknames = [(name, fuzz.token_set_ratio(name.lower(), qlower)) for name in
               list(stopnames.keys())]
    toprank = [tup for tup in ranknames if tup[1] > 40]
    if len(toprank) == 0:
        return render_template("badsearch.html")
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
                           table=sTable if len(stdat) > 0 else "")


@app.route("/route/")
def ttSearch():
    ra = request.args
    if "r" in ra:
        return redirect("/route/{}/".format(ra["r"].strip()), 302, None)
    else:
        return redirect("/", 302, None)


@app.route("/route/<string:rquery>/")
def routeInfo(rquery):
    updateRouteInfo()
    if rquery == "" or rquery not in routelist:
        return render_template("badroute.html", error="No such route",
                               routes=sortedRouteCodes())
    routeinfo = routelist[rquery]
    req = requests.get(stoplisturl, params={"route_id": routeinfo["route_id"]}, headers=headers)
    if req.status_code != 200:
        # return "Error {}".format(req.status_code)
        return render_template("badroute.html", error=req.status_code,
                               routes=sortedRouteCodes())
    rv = req.json()
    if len(rv) == 0:
        return render_template("badroute.html", error="No stops in route",
                               routes=sortedRouteCodes())
    route_code = routeinfo["route_short_name"]
    route_name = routeinfo["route_long_name"]
    rstopsdat = [{"code": stop["stop_id"],
                  "sms": stop["parent_station"] if
                  stop["parent_station"] != "" else stop["stop_id"],
                  "stop": stop["stop_name"],
                  "zone": stop["zone_id"]} for stop in rv]
    rTable = StopTable(rstopsdat)
    return render_template("route.html", code=route_code, name=route_name,
                           table=rTable if len(rstopsdat) > 0 else "",
                           routes=sortedRouteCodes())


if __name__ == "__main__":
    app.run()
