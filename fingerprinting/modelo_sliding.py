"""Modelos de localizacion con ventanas deslizantes y validacion cruzada por escena."""
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR
from sklearn.multioutput import MultiOutputRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from xgboost import XGBRegressor
import torch
from torch import nn

W = 10

def make_windows(df, anchors, w=W):
    cols = [f"anchor_{a}_distance_cm_median" for a in anchors]
    out = []
    for scene, g in df.groupby("label", sort=False):
        g = g.sort_values("timestamp_inicio").reset_index(drop=True)
        values = g[cols].to_numpy(float)
        xy = g[["x", "y"]].to_numpy(float)
        for end in range(len(g)):
            start = max(0, end - w + 1)
            seq = values[start:end + 1]
            # Cada placa conserva su ultima muestra valida dentro de la ventana.
            latest = np.full(len(anchors), -1.0)
            for j in range(len(anchors)):
                valid = seq[:, j][seq[:, j] != -1]
                if len(valid): latest[j] = valid[-1]
            if np.all(latest == -1):
                continue
            padded = np.full((w, len(anchors)), -1.0)
            padded[-len(seq):] = seq
            out.append({"scene": scene, "time": g.loc[end, "timestamp_inicio"],
                        "x": xy[end, 0], "y": xy[end, 1], "latest": latest,
                        "sequence": padded})
    return out

class GRU2(nn.Module):
    def __init__(self, n):
        super().__init__(); self.gru = nn.GRU(n, 32, num_layers=2, batch_first=True); self.fc = nn.Linear(32, 2)
    def forward(self, x): return self.fc(self.gru(x)[0][:, -1])

def train_gru(train, test, n, epochs=20, batch=4):
    torch.manual_seed(7); model = GRU2(n); opt = torch.optim.Adam(model.parameters(), lr=1e-3); loss = nn.MSELoss()
    X = torch.tensor(np.stack([r["sequence"] for r in train]), dtype=torch.float32)
    y = torch.tensor(np.array([[r["x"], r["y"]] for r in train]), dtype=torch.float32)
    for _ in range(epochs):
        order = torch.randperm(len(X))
        for ix in order.split(batch):
            opt.zero_grad(); l = loss(model(X[ix]), y[ix]); l.backward(); opt.step()
    with torch.no_grad(): pred = model(torch.tensor(np.stack([r["sequence"] for r in test]), dtype=torch.float32)).numpy()
    return pred

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--segmentacion", type=Path, required=True); ap.add_argument("--salida", type=Path, required=True); args = ap.parse_args()
    args.salida.mkdir(parents=True, exist_ok=True); df = pd.read_csv(args.segmentacion)
    suffix = "_distance_cm_median"
    anchors = sorted({c[len("anchor_"):-len(suffix)] for c in df.columns if c.startswith("anchor_") and c.endswith(suffix)})
    windows = make_windows(df, anchors); scenes = sorted({r["scene"] for r in windows})
    total = sum(len(df[df.label == s]) for s in df.label.unique()); valid = len(windows)
    print(f"Ventanas validas: {valid}; ventanas todo -1 descartadas: {total-valid}; porcentaje descartado: {100*(total-valid)/max(1,total):.2f}%")
    results = []
    for model_name in ("xgboost", "svm", "gru"):
        all_true=[]; all_pred=[]
        for held in scenes:
            tr=[r for r in windows if r["scene"] != held]; te=[r for r in windows if r["scene"] == held]
            if not tr or not te: continue
            if model_name == "xgboost":
                model=MultiOutputRegressor(XGBRegressor(n_estimators=250,max_depth=4,learning_rate=.05,subsample=.9,colsample_bytree=.9,objective="reg:squarederror",n_jobs=2))
                model.fit(np.stack([r["latest"] for r in tr]), np.array([[r["x"],r["y"]] for r in tr])); pred=model.predict(np.stack([r["latest"] for r in te]))
            elif model_name == "svm":
                model=make_pipeline(StandardScaler(), MultiOutputRegressor(SVR(C=10, gamma="scale")))
                model.fit(np.stack([r["latest"] for r in tr]), np.array([[r["x"],r["y"]] for r in tr])); pred=model.predict(np.stack([r["latest"] for r in te]))
            else: pred=train_gru(tr,te,len(anchors))
            true=np.array([[r["x"],r["y"]] for r in te]); all_true.append(true); all_pred.append(pred)
            plt.figure(figsize=(9,4)); plt.plot(true[:,0],true[:,1],"r.-",label="ground truth"); plt.plot(pred[:,0],pred[:,1],"b.-",label="predicción"); plt.xlabel("x"); plt.ylabel("y"); plt.title(f"{model_name} | test={held}"); plt.legend(); plt.grid(True); plt.tight_layout(); plt.savefig(args.salida/f"{model_name}_{held}.png",dpi=150); plt.close()
        yt=np.vstack(all_true); yp=np.vstack(all_pred); results.append({"model":model_name,"mae":float(mean_absolute_error(yt,yp)),"rmse":float(np.sqrt(mean_squared_error(yt,yp))),"n":len(yt)})
    (args.salida/"metrics.json").write_text(json.dumps({"W":W,"time_step":1,"models":results},indent=2),encoding="utf-8")
    print(json.dumps(results, indent=2))

if __name__ == "__main__": main()
