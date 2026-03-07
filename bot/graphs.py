import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

def generate_vpn_graph(peers_count: int):
    # Динамически получаем количество из АПИ
    x =[0, 1, 2]
    y =[peers_count, peers_count, peers_count] 

    plt.figure()
    plt.plot(x, y)
    plt.title(f"Active Peers: {peers_count}")
    
    path = "/volumes/backups/vpn_graph.png"
    plt.savefig(path)
    plt.close()

    return path