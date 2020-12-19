from flask import Flask, render_template
from flask_table import Table, Col
from datetime import datetime
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


class Item(object):
    def __init__(self, route, dest, sched, est):
        self.route = route
        self.dest = dest
        self.sched = sched
        self.est = est
        self.status = status



app = Flask(__name__)

stopurl = "https://www.metlink.org.nz/api/v1/StopDepartures/"

@app.route("/")
def rti():
    return "Hello world!"

@app.route("/stop/<string:stop>/")
def timetable(stop):
    req = requests.get("{}{}".format(stopurl, stop))
    if req.status_code != 200:
        return "Error {}".format(req.status_code)
    # return req.json()
    rv = req.json()
    lastup = datetime.fromisoformat(rv["LastModified"])
    ttdat = [{"route" : s["ServiceID"], "dest" : s["DestinationStopName"],
              "sched" :
              datetime.fromisoformat(s["AimedDeparture"]).strftime("%H:%M"),
              "est" : "" if s["ExpectedDeparture"] is None else "{} mins".format((datetime.fromisoformat(s["ExpectedDeparture"]) -
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
