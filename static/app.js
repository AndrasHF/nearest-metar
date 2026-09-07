const $ = (id) => document.getElementById(id);
const button = $("locate");
const statusBox = $("status");
const weather = $("weather");
const stationSelect = $("station-select");
let nearbyStations = [];

function showStatus(message, error = false) {
  statusBox.textContent = message;
  statusBox.hidden = false;
  statusBox.classList.toggle("error", error);
}

function value(number, suffix = "") {
  return number == null ? "—" : `${number}${suffix}`;
}

function render(data) {
  $("station").textContent = data.station;
  $("distance").textContent = data.distance_km < 1 ? `${Math.round(data.distance_km * 1000)} M` : `${data.distance_km} KM`;
  $("temperature").textContent = value(data.temperature_c);
  $("category").textContent = data.flight_category || "UNREPORTED";
  $("observed").textContent = data.observed_at ? `OBSERVED ${new Date(data.observed_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}` : "Observation time unavailable";
  const direction = data.wind_direction === "0" || data.wind_direction === 0 ? "VRB" : (data.wind_direction ? `${data.wind_direction}°` : "—");
  $("wind").textContent = data.wind_speed_kt == null ? "—" : `${direction} / ${data.wind_speed_kt}${data.wind_gust_kt == null ? "" : ` G${data.wind_gust_kt}`} KT`;
  $("visibility").textContent = value(data.visibility_mi, " SM");
  $("dewpoint").textContent = value(data.dewpoint_c, " °C");
  $("altimeter").textContent = value(data.altimeter_in_hg, " inHg");
  $("raw").textContent = data.raw || "Raw observation unavailable";
  $("station-name").textContent = data.station_name || "Station name unavailable";
  $("station-location").textContent = [data.station_state, data.station_country].filter(Boolean).join(", ") || "Location unavailable";
  $("station-position").textContent = `${Number(data.latitude).toFixed(3)}°, ${Number(data.longitude).toFixed(3)}°`;
  $("station-elevation").textContent = value(data.station_elevation_m, " M");
  $("station-iata").textContent = data.station_iata || "—";
  statusBox.hidden = true;
  weather.hidden = false;
}

function renderStations(stations) {
  nearbyStations = stations;
  stationSelect.replaceChildren(...stations.map((station, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    const place = [station.station_name, station.station_state, station.station_country].filter(Boolean).join(" · ");
    option.textContent = `${station.station}${place ? ` — ${place}` : ""} · ${station.distance_km} km`;
    return option;
  }));
  render(stations[0]);
  weather.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadWeather(position) {
  const { latitude, longitude } = position.coords;
  showStatus("Position found. Loading the nearest observation…");
  try {
    const response = await fetch(`/api/metar?lat=${encodeURIComponent(latitude)}&lon=${encodeURIComponent(longitude)}&lang=${encodeURIComponent(navigator.language || "en")}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Weather data could not be loaded.");
    if (!Array.isArray(data.stations) || data.stations.length === 0) throw new Error("No nearby METAR stations were found.");
    $("browser-location-name").textContent = data.browser_location?.name || `${latitude.toFixed(4)}, ${longitude.toFixed(4)}`;
    $("browser-location").hidden = false;
    renderStations(data.stations);
  } catch (error) {
    showStatus(error.message, true);
  } finally {
    button.disabled = false;
    button.querySelector("span").textContent = "Refresh location";
  }
}

button.addEventListener("click", () => {
  weather.hidden = true;
  button.disabled = true;
  button.querySelector("span").textContent = "Locating…";
  if (!navigator.geolocation) {
    showStatus("This browser does not support location access.", true);
    button.disabled = false;
    return;
  }
  navigator.geolocation.getCurrentPosition(loadWeather, (error) => {
    const messages = { 1: "Location access was denied. Allow it in your browser settings and try again.", 2: "Your location is currently unavailable.", 3: "Finding your location took too long. Please try again." };
    showStatus(messages[error.code] || "Your location could not be found.", true);
    button.disabled = false;
    button.querySelector("span").textContent = "Try again";
  }, { enableHighAccuracy: false, timeout: 12000, maximumAge: 300000 });
});

stationSelect.addEventListener("change", () => {
  const station = nearbyStations[Number(stationSelect.value)];
  if (station) render(station);
});

$("copy").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("raw").textContent);
  $("copy").textContent = "COPIED";
  setTimeout(() => { $("copy").textContent = "COPY"; }, 1500);
});
