# %%
import argparse
import numpy as np
import os
import scipy as sci
from sklearn.metrics import max_error
import time
import torch
import torch.nn as nn
from torch import Tensor


# %%
# Parse arguments
argparser = argparse.ArgumentParser()
argparser.add_argument("--num-basis", type=int, default=1000, help="The number of basis/feature functions (default: 1000).")
argparser.add_argument("--scale", type=float, default=0.5, help="The scale of NN (default: 0.5).")
argparser.add_argument("--seed", type=int, default=2024, help="The random seed (default: 2024).")
argparser.add_argument("--save-dir", type=str, default="checkpoint/Poisson-PS", help="The directory path to save model (default: checkpoint/Poisson-PS).")
args = argparser.parse_args()
print(args)

# Define the dimension of equation
n_basis_func = args.num_basis


# %%
# set random seed and deterministic behavior
seed = args.seed
torch.manual_seed(seed)
torch.cuda.manual_seed(seed)
np.random.seed(seed)
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False
torch.set_default_dtype(torch.float64)
start_time = time.time()


# %%
# Define the network architecture
class Net(nn.Module):
    def __init__(self, hidden_size, scale):
        # hidden_size: The hidden layer size that is equal to the number of basis/feature functions
        super(Net, self).__init__()
        self.input_layers = torch.nn.Linear(2, hidden_size)
        self.input_layers.weight.data.uniform_(-scale, scale)
        self.input_layers.bias.data.uniform_(-scale, scale)
        print("The shape of input layer weights:", self.input_layers.weight.shape)

    def forward(self, x):
        y = torch.sin(self.input_layers(x))
        return y
    
net = Net(n_basis_func, args.scale)


# %%
# Define dimension and boundary of PDE
d = 2 # The input dimension of the PDE
xl = 0.0 # left boundary.
xr = 1.0 # right boundary.

# Sample interior points
n_in = 1000 # The number of interior points
points_in = np.random.uniform(xl, xr, (n_in, d))
X = torch.from_numpy(points_in)
X.requires_grad_(True)

# Sample boundary points
n_bc = 100 # The number of boundary points
points_bc_raw = np.random.uniform(xl, xr, (2*n_bc*d, d))
for i in range(n_bc):
    # range for this is [i * 2d, (i + 1) * 2d]
    for j in range(d):
        points_bc_raw[i*2*d+j, j] = xl
        points_bc_raw[i*2*d+d+j, j] = xr
points_bc = points_bc_raw
X_bound = torch.from_numpy(points_bc)


# %%
# Calculate first and second order derivatives
dx = []
dy = []
dxx = []
dyy = []
for i in range(n_basis_func):
    d = torch.autograd.grad(
        net(X)[:,i : i + 1], 
        X, 
        grad_outputs=torch.ones_like(net(X)[:, i:i + 1]), 
        create_graph=True)[0]
    dx.append(d[:,0:1].detach())      
    dy.append(d[:,1:2].detach())

    # The second derivative with respect to x
    dx2 = torch.autograd.grad(d[:,0:1], 
                               X, 
                               grad_outputs=torch.ones_like(d[:,0:1]), 
                               create_graph=True)[0]
    dxx.append(dx2[:,0:1].detach())

    # The second derivative with respect to y
    dy2 = torch.autograd.grad(d[:,1:2], 
                               X, 
                               grad_outputs=torch.ones_like(d[:,1:2]), 
                               create_graph=True)[0]
    dyy.append(dy2[:,1:2].detach())

dx = torch.cat(dx,dim=1)
dy = torch.cat(dy,dim=1) # [n，m]
dxx = torch.cat(dxx,dim=1)
dyy = torch.cat(dyy,dim=1) # [n, m]


# %% Ground Truth
# The expression of ground truth
def real_solution(p: Tensor):
    x = p[:, 0:1]
    u = torch.where(x<0.5, x.pow(2), (x-1).pow(2))
    return u.reshape(-1, 1).detach()

# The expression of boundary condition
def boundary(p: Tensor):
    return real_solution(p)

# The expression of initial condition
def initial(p: Tensor):
    return real_solution(p)


# %% Construct loss function
# The interior points of eqution
u_domain_dxx_pred = dxx.detach().cpu().numpy()
u_domain_dyy_pred = dyy.detach().cpu().numpy()
f_domain_exact = -2 * np.ones((X.shape[0], 1)) # The value of the right side of the equation at the interior points

# The boundary condition: u(x,0), u(x, 1)
u_bound_pred = net(X_bound) # The left hand side of initial condition
u_bound_pred = u_bound_pred.detach().cpu().numpy()
u_bound_exact = boundary(X_bound).detach().cpu().numpy()

# AC = f
A = np.vstack([-u_domain_dxx_pred-u_domain_dyy_pred, u_bound_pred])
f = np.vstack([f_domain_exact, u_bound_exact])


# %%
# Perform least-squares approximation to obtain coefficient matrix C
C = sci.linalg.lstsq(A, f)[0] # (M，1)


# %%
# Calculate Metrics
u_pred = (net(X).detach().cpu().numpy() @ C) # Calculate predicted values
u_domain_exact = real_solution(X).detach().cpu().numpy() # # Calculate the ground truth values of collection points

# Calculate L2 relative error
rel_error = np.linalg.norm(u_pred - u_domain_exact, 2) / np.linalg.norm(u_domain_exact, 2)
print("Relative Error: ", rel_error)

# Calculate max error
Max_error = max_error(u_pred, u_domain_exact)
print("Max Error:", Max_error)


# %%
# Record execution time
end_time = time.time()
execution_time = end_time - start_time
print(f"Execution time: {execution_time}s")


# %%
# Save basis/feature functions and coefficient matrix C
if not os.path.exists(args.save_dir):
    os.makedirs(args.save_dir)
torch.save(net.state_dict(), os.path.join(args.save_dir, f"model_{args.num_basis}basis_scale{args.scale}_seed{args.seed}.pth"))
np.save(os.path.join(args.save_dir, f"C_{args.num_basis}basis_scale{args.scale}_seed{args.seed}.npy"), C)