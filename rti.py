from flask import Flask, render_template, request, redirect
from flask_table import Table, Col, LinkCol
from dateutil.parser import isoparse
import requests
import re
from urllib.parse import quote

depStatus = {
    "onTime": "On time",
    "ontime": "On time",
    "early": "Early",
    "delayed": "Late",
    "cancelled": "Cancelled"
}


class TimeTable(Table):
    route = Col("Route", th_html_attrs={"title": "Route"})
    dest = Col("Dest", th_html_attrs={"title": "Destination"})
    sched = Col("Sched", th_html_attrs={"title": "Scheduled departure time"})
    status = Col("Status", th_html_attrs={"title": "Status"})
    est = Col("Est", th_html_attrs={"title": "Estimated time until departure"})
    table_id = "stoptimetable"


class SearchTable(Table):
    code = LinkCol("Code", "timetable", url_kwargs=dict(stop="sms"),
                   attr='code')
    stop = Col("Stop")
    table_id = "searchtable"


app = Flask(__name__)

stopurl = "https://www.metlink.org.nz/api/v1/StopDepartures/"
searchurl = "https://www.metlink.org.nz/api/v1/StopSearch/"


def stopExtract(code, name):
    z = re.match("{} - (.*)".format(code), name)
    return z.groups()[0]


def minCompare(comp, origin):
    return 0 if comp <= origin else (comp - origin).seconds // 60

def statusString(service):
    status = service["DepartureStatus"]
    if status in depStatus:
        aimedD = service["AimedDeparture"]
        estD = service["ExpectedDeparture"]
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
    return render_template("main.html")


@app.route("/stop/")
def ttSearch():
    ra = request.args
    if "stopnum" in ra:
        return redirect("/stop/{}/".format(ra["stopnum"]), 302, None)
    else:
        return redirect("/", 302, None)


@app.route("/stop/<string:stop>/")
def timetable(stop):
    req = requests.get("{}{}".format(stopurl, stop))
    if req.status_code != 200:
        # return "Error {}".format(req.status_code)
        return render_template("nostop.html", error=req.status_code)
    # return req.json()
    rv = req.json()
    lastup = isoparse(rv["LastModified"])
    if "Services" in rv:
        ttdat = [{"route": s["ServiceID"], "dest": s["DestinationStopName"],
                  "sched":
                  isoparse(s["AimedDeparture"]).strftime("%H:%M"),
                  "est": "" if s["ExpectedDeparture"] is None else
                      "{} mins".format(minCompare(isoparse(s["ExpectedDeparture"]),
                                                  lastup)),
                  "status": statusString(s)}
                 for s in rv["Services"]]
        tTable = TimeTable(ttdat)
    else:
        ttdat = []
    return render_template("stop.html", stopnumber=stop,
                           stopname=rv["Stop"]["Name"],
                           lup=lastup.strftime("%H:%M:%S, %A %B %-d"),
                           table=tTable if len(ttdat) > 0 else None,
                           notices=[n["LineNote"] for n in rv["Notices"]] if "Notices" in rv else None)


@app.route("/search/")
def stopsearch():
    query = request.args["q"] if "q" in request.args else ""
    req = requests.get("{}{}".format(searchurl, quote(query)))
    if req.status_code != 200:
        return render_template("badsearch.html", error=req.status_code)
    stdat = [{"code": s["Sms"], "sms": s["Sms"], "stop":
              stopExtract(s["Sms"], s["Name"])} for s
             in req.json()]
    sTable = SearchTable(stdat)
    # return sTable.__html__()
    return render_template("search.html", searchstring=query,
                           numres=len(stdat),
                           table=sTable if len(stdat) > 0 else "")


if __name__ == "__main__":
    app.run()
