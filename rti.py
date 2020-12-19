from flask import Flask, render_template, request, redirect
from flask_table import Table, Col
from datetime import datetime
from dateutil.parser import isoparse
import requests

depStatus = {
    "onTime": "On time",
    "early": "Early",
    "delayed": "Late"
}

class TimeTable(Table):
    route = Col("Route")
    dest = Col("Dest")
    sched = Col("Sched")
    est = Col("Est")
    status = Col("Status")
    table_id = "stoptimetable"


app = Flask(__name__)

stopurl = "https://www.metlink.org.nz/api/v1/StopDepartures/"

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
        return render_template("nostop.html", error = req.status_code)
    # return req.json()
    rv = req.json()
    lastup = isoparse(rv["LastModified"])
    ttdat = [{"route" : s["ServiceID"], "dest" : s["DestinationStopName"],
              "sched" :
              isoparse(s["AimedDeparture"]).strftime("%H:%M"),
              "est" : "" if s["ExpectedDeparture"] is None else "{} mins".format((isoparse(s["ExpectedDeparture"]) -
                       lastup).seconds // 60),
              "status" : depStatus[s["DepartureStatus"]] if
              s["DepartureStatus"] in depStatus else s["DepartureStatus"]}
             for s in rv["Services"]]
    tTable = TimeTable(ttdat)
    return render_template("stop.html", stopnumber = stop,
                           stopname = rv["Stop"]["Name"],
                           lup = lastup.strftime("%H:%M:%S, %A %B %-d %Y"),
                          table = tTable)

if __name__ == "__main__":
    app.run()
