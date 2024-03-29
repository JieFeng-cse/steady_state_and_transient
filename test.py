### import collections
from cProfile import label
import matplotlib.pyplot as plt
import matplotlib
import numpy as np
from numpy import linalg as LA

from scipy.io import loadmat

import torch
import torch.nn.functional as F
import argparse

from environment_single_phase import create_56bus, VoltageCtrl_nonlinear
from env_single_phase_13bus import IEEE13bus, create_13bus
from env_single_phase_123bus import IEEE123bus, create_123bus
from safeDDPG import ValueNetwork, SafePolicyNetwork, DDPG, PolicyNetwork, SafePolicy3phase, LinearPolicy
from IEEE_13_3p import IEEE13bus3p, create_13bus3p
from tqdm import tqdm


use_cuda = torch.cuda.is_available()
device   = torch.device("cuda" if use_cuda else "cpu")

parser = argparse.ArgumentParser(description='Single Phase Safe DDPG')
parser.add_argument('--env_name', default="13bus",
                    help='name of the environment to run')
parser.add_argument('--algorithm', default='safe-ddpg', help='name of algorithm')
parser.add_argument('--safe_type', default='three_single')
parser.add_argument('--safe_method', default='safe-flow') 
parser.add_argument('--use_safe_flow', default='True') 
parser.add_argument('--use_gradient', default='True') 
args = parser.parse_args()
seed = 10
torch.manual_seed(seed)
plt.rcParams['font.size'] = '20'

"""
Create Agent list and replay buffer
"""
max_ac = 0.3
ph_num = 1
slope = 2
if args.env_name == '56bus':
    pp_net = create_56bus()
    injection_bus = np.array([18, 21, 30, 45, 53])-1  
    env = VoltageCtrl_nonlinear(pp_net, injection_bus)
    num_agent = 5
if args.env_name == '13bus':
    pp_net = create_13bus()
    injection_bus = np.array([2, 7, 9])
    env = IEEE13bus(pp_net, injection_bus)
    num_agent = 3
    # Q_limit = np.asarray([[-1.0,1.0],[-1.0,0.8],[-1.0,0.6]])
    Q_limit = np.asarray([[-1.5,1.5],[-1.4,1.4],[-1.0,0.6]])
    C = np.asarray([0.7,0.5,0.6])*0.15
    alpha = 0.5
if args.env_name == '123bus':
    pp_net = create_123bus()
    injection_bus = np.array([10, 11, 16, 20, 33, 36, 48, 59, 66, 75, 83, 92, 104, 61])-1
    env = IEEE123bus(pp_net, injection_bus)
    num_agent = 14
    # Q_limit = np.asarray([[-15,15],[-10,10],[-13,13],[-7,7],[-6,6],[-3.5,3.5],[-7,7],[-2.5,2.5],[-3,3],[-4.5,4.5],[-1.5,1.5],[-3,3],[-2.4,2.4],[-1.2,1.2]])
    Q_limit = np.asarray([[-21.6,21.6],[-18,18],[-21.6,21.6],[-10.8,10.8],[-9.45,9.45],[-20,20],[-20,20],[-16,16],[-4.725,4.725],[-7.2,7.2],[-7.2,7.2],[-6.75,6.75],[-6.75,6.75],[-5.4,5.4]])
    # C = np.asarray([0.1,0.2,0.3,0.3,0.5,0.7,1.0,0.7,1.0,1.0,1.0,1.0,0.5,0.7])*0.025
    C = np.asarray([0.2,0.25,0.1,0.3,0.3,0.2,0.2,0.3,0.9,0.7,0.7,0.7,0.6,0.7])*0.02
    if args.safe_method == 'project':
        alpha = 1
    else:
        alpha =0.5
    max_ac = 0.8
    slope = 5
if args.env_name == '13bus3p':
    # injection_bus = np.array([675,633,680])
    injection_bus = np.array([633,634,671,645,646,692,675,611,652,632,680,684])
    pp_net, injection_bus_dict = create_13bus3p(injection_bus) 
    max_ac = 0.4
    env = IEEE13bus3p(pp_net,injection_bus_dict)
    num_agent = len(injection_bus)
    ph_num=3
    # slope = 3

if ph_num == 3:
    type_name = 'three-phase'
else:
    type_name = 'single-phase'

obs_dim = env.obs_dim
action_dim = env.action_dim
hidden_dim = 100


ddpg_agent_list = []
safe_ddpg_agent_list = []
linear_agent_list = []

for i in range(num_agent):
    if not ph_num == 3:
        safe_ddpg_value_net  = ValueNetwork(obs_dim=obs_dim, action_dim=action_dim, hidden_dim=hidden_dim).to(device)    
        safe_ddpg_policy_net = SafePolicyNetwork(env=env, obs_dim=obs_dim, action_dim=action_dim, hidden_dim=hidden_dim, \
            up=Q_limit[i,1],low=Q_limit[i,0],alpha=alpha,node_cost=C[i],\
                use_gradient=(args.use_gradient=='True'), safe_flow=(args.use_safe_flow=='True')).to(device)
    else:
        if ph_num == 3:
            obs_dim = len(env.injection_bus[env.injection_bus_str[i]])
            action_dim = obs_dim
        safe_ddpg_policy_net = SafePolicy3phase(env, obs_dim, action_dim, hidden_dim, env.injection_bus_str[i]).to(device)
        safe_ddpg_value_net  = ValueNetwork(obs_dim=obs_dim, action_dim=action_dim, hidden_dim=hidden_dim).to(device)  
    
    ddpg_value_net  = ValueNetwork(obs_dim=obs_dim, action_dim=action_dim, hidden_dim=hidden_dim).to(device)  
    ddpg_policy_net = SafePolicyNetwork(env=env, obs_dim=obs_dim, action_dim=action_dim, hidden_dim=hidden_dim, \
            up=Q_limit[i,1],low=Q_limit[i,0],alpha=alpha,node_cost=C[i],\
                use_gradient=False, safe_flow=(args.use_safe_flow=='True')).to(device)

    ddpg_agent = DDPG(policy_net=ddpg_policy_net, value_net=ddpg_value_net,
                 target_policy_net=ddpg_policy_net, target_value_net=ddpg_value_net)
    
    linear_value_net  = ValueNetwork(obs_dim=obs_dim, action_dim=action_dim, hidden_dim=hidden_dim).to(device)  
    linear_policy_net = LinearPolicy(env=env, ph_num=ph_num, \
            up=Q_limit[i,1],low=Q_limit[i,0],alpha=alpha,node_cost=C[i],\
                use_gradient=(args.use_gradient=='True'), safe_flow=(args.use_safe_flow=='True')).to(device)  

    linear_agent = DDPG(policy_net=linear_policy_net, value_net=linear_value_net,
                 target_policy_net=linear_policy_net, target_value_net=linear_value_net)
    
    safe_ddpg_agent = DDPG(policy_net=safe_ddpg_policy_net, value_net=safe_ddpg_value_net,
                 target_policy_net=safe_ddpg_policy_net, target_value_net=safe_ddpg_value_net)    
    
    ddpg_agent_list.append(ddpg_agent)
    safe_ddpg_agent_list.append(safe_ddpg_agent)
    linear_agent_list.append(linear_agent)

for i in range(num_agent):
    # ddpg_policynet_dict = torch.load(f'checkpoints/{type_name}/{args.env_name}/ddpg/{args.safe_method}_policy_net_checkpoint_a{i}.pth')
    # ddpg_agent_list[i].policy_net.load_state_dict(ddpg_policynet_dict)

    # linear_policynet_dict = torch.load(f'checkpoints/{type_name}/{args.env_name}/linear/{args.safe_method}_policy_net_checkpoint_a{i}.pth')
    # linear_agent_list[i].policy_net.load_state_dict(linear_policynet_dict)
    ddpg_policynet_dict = torch.load(f'checkpoints/{type_name}/{args.env_name}/safe-ddpg/no_gradient_policy_net_checkpoint_a{i}.pth')
    ddpg_agent_list[i].policy_net.load_state_dict(ddpg_policynet_dict)

    linear_policynet_dict = torch.load(f'checkpoints/{type_name}/{args.env_name}/linear/{args.safe_method}_policy_net_checkpoint_a{i}.pth')
    linear_agent_list[i].policy_net.load_state_dict(linear_policynet_dict)

    if ph_num == 3:
        safe_ddpg_policynet_dict = torch.load(f'checkpoints/{type_name}/{args.env_name}/safe-ddpg/{args.safe_type}/policy_net_checkpoint_a{i}.pth')
        safe_ddpg_agent_list[i].policy_net.load_state_dict(safe_ddpg_policynet_dict)
    else:
        safe_ddpg_policynet_dict = torch.load(f'checkpoints/{type_name}/{args.env_name}/safe-ddpg/{args.safe_method}_policy_net_checkpoint_a{i}.pth')
        safe_ddpg_agent_list[i].policy_net.load_state_dict(safe_ddpg_policynet_dict)

def plot_action():
    
    fig, axs = plt.subplots(1, 1, figsize=(16,12))
    for i in range(num_agent):
        # plot policy
        N = 40
        s_array = np.zeros(N,)
        
        a_array_baseline = np.zeros(N,)
        safe_ddpg_a_array = np.zeros(N,)
        
        for j in range(N):
            state = np.array([0.8+0.01*j])
            s_array[j] = state

            safe_ddpg_action = safe_ddpg_agent_list[i].policy_net.get_action(np.asarray([state]))
            safe_ddpg_action = np.clip(safe_ddpg_action, -max_ac, max_ac)
            safe_ddpg_a_array[j] = -safe_ddpg_action

        axs.plot(s_array, safe_ddpg_a_array, label = f'stable-DDPG at {injection_bus[i]}')
    for i in range(num_agent):
        # plot policy
        N = 40
        s_array = np.zeros(N,)
        
        a_array_baseline = np.zeros(N,)
        ddpg_a_array = np.zeros(N,)
        safe_ddpg_a_array = np.zeros(N,)
        
        for j in range(N):
            state = np.array([0.8+0.01*j])
            s_array[j] = state

            action_baseline = (np.maximum(state-1.03, 0)-np.maximum(0.97-state, 0)).reshape((1,))
        
            ddpg_action = ddpg_agent_list[i].policy_net.get_action(np.asarray([state]))
            ddpg_action = np.clip(ddpg_action, -max_ac, max_ac)
            a_array_baseline[j] = -action_baseline[0]
            ddpg_a_array[j] = -ddpg_action

        
        axs.plot(s_array, ddpg_a_array, '--', label = f'DDPG at {injection_bus[i]}')
    axs.plot(s_array, 2*a_array_baseline, '-.', label = 'Linear',color='r')
    axs.legend(loc='lower left', prop={"size":20})
    plt.show()

def plot_traj():
    ddpg_plt=[]
    safe_plt = []
    ddpg_a_plt=[]
    safe_a_plt = []

    state = env.reset()
    episode_reward = 0
    last_action = np.zeros((num_agent,1))
    action_list=[]
    state_list =[]
    state_list.append(state)
    for step in range(40):
        action = []
        for i in range(num_agent):
            # sample action according to the current policy and exploration noise
            action_agent = ddpg_agent_list[i].policy_net.get_action(np.asarray([state[i]]),last_action[i])#+np.random.normal(0, 0.05)
            action.append(action_agent)

        # PI policy    
        action = last_action + np.asarray(action)

        # execute action a_t and observe reward r_t and observe next state s_{t+1}
        next_state, _, _, done = env.step_Preward(action, (action-last_action))
        if done:
            print("finished")
        action_list.append(action)
        state_list.append(next_state)
        last_action = np.copy(action)
        state = next_state
    fig, axs = plt.subplots(1, 2, figsize=(16,7))
    for i in range(num_agent):    
        dps = axs[0].plot(range(len(action_list)), np.array(state_list)[:len(action_list),i], '-.', label = f'DDPG at {injection_bus[i]}', linewidth=2)
        dpa = axs[1].plot(range(len(action_list)), np.array(action_list)[:,i], '-.', label = f'DDPG at {injection_bus[i]}', linewidth=2)
        ddpg_plt.append(dps)
        ddpg_a_plt.append(dpa)

    state = env.reset()
    last_action = np.zeros((num_agent,1))
    action_list=[]
    state_list =[]
    state_list.append(state)
    for step in range(40):
        action = []
        for i in range(num_agent):
            # sample action according to the current policy and exploration noise
            action_agent = safe_ddpg_agent_list[i].policy_net.get_action(np.asarray([state[i]]),last_action[i])#+np.random.normal(0, 0.05)
            # action_agent = (np.maximum(state[i]-1.05, 0)-np.maximum(0.95-state[i], 0)).reshape((1,))*2
            action.append(action_agent)

        # PI policy    
        action = last_action + np.asarray(action)

        # execute action a_t and observe reward r_t and observe next state s_{t+1}
        next_state, _, _, done = env.step_Preward(action, (last_action-action))
        if done:
            print("finished")
        action_list.append(action)
        state_list.append(next_state)
        last_action = np.copy(action)
        state = next_state
    safe_name = []
    for i in range(num_agent):    
        safes=axs[0].plot(range(len(action_list)), np.array(state_list)[:len(action_list),i], '-', label = f'Stable-DDPG at {injection_bus[i]}', linewidth=2)
        safea=axs[1].plot(range(len(action_list)), np.array(action_list)[:,i], label = f'Stable-DDPG at {injection_bus[i]}', linewidth=2)
        safe_plt.append(safes)
        safe_name.append(f'Stable-DDPG at {injection_bus[i]}')
        safe_a_plt.append(safea)
    # leg1 = plt.legend(safe_a_plt, safe_name, loc='lower left')
    axs[0].legend(loc='upper right', prop={"size":20})
    axs[1].legend(loc='lower left', prop={"size":20})
    axs[0].set_xlabel('Steps')   
    axs[1].set_xlabel('Steps')  
    axs[0].set_ylabel('Bus Voltage [p.u.]')   
    axs[1].set_ylabel('Reactive Power Injection [MVar]')  
    plt.tight_layout()
    plt.show()

#test success rate, voltage violation after 40 steps
def test_suc_rate(algm, step_num=60):
    success_num = 0
    final_state_list = []
    final_step_list = []
    control_cost_list = []
    final_reward_list = []
    for rep in tqdm(range(500)):
        state = env.reset(rep)
        last_action = np.zeros((num_agent,ph_num))
        action_list=[]
        state_list =[]
        state_list.append(state)
        control_action = []
        gamma = 0.99
        for step in range(step_num):
            action = []
            for i in range(num_agent):
                if ph_num==3:
                    action_agent = np.zeros(3)
                    phases = env.injection_bus[env.injection_bus_str[i]]
                    id = get_id(phases)
                    if algm == 'linear':
                        action_tmp = linear_agent_list[i].policy_net.get_action(np.asarray([state[i,id]])) 
                        action_tmp = action_tmp.reshape(len(id),)  
                    elif algm == 'safe-ddpg':
                        action_tmp = safe_ddpg_agent_list[i].policy_net.get_action(np.asarray([state[i,id]])) 
                        action_tmp = action_tmp.reshape(len(id),)  
                    elif algm == 'ddpg':
                        action_tmp = ddpg_agent_list[i].policy_net.get_action(np.asarray([state[i,id]])) 
                        action_tmp = action_tmp.reshape(len(id),)  
                    # if algm != 'linear':
                    for p in range(len(phases)):
                        action_agent[id[p]]=action_tmp[p]
                    action_agent = np.clip(action_agent, -max_ac, max_ac) 
                    action.append(action_agent)
                # sample action according to the current policy and exploration noise
                else:
                    if algm == 'linear':
                        action_agent = linear_agent_list[i].policy_net.get_action(np.asarray([state[i]]),last_action[i]) 
                    elif algm == 'safe-ddpg':
                        action_agent = safe_ddpg_agent_list[i].policy_net.get_action(np.asarray([state[i]]),last_action[i])
                    elif algm == 'ddpg':
                        action_agent = ddpg_agent_list[i].policy_net.get_action(np.asarray([state[i]]),last_action[i])
                    # 
                    # action_agent = np.clip(action_agent, -max_ac, max_ac)
                    action.append(action_agent)

            # PI policy    
            action = last_action + np.asarray(action)
            
            # execute action a_t and observe reward r_t and observe next state s_{t+1}
            next_state, reward, reward_sep, done = env.step_Preward(action, (action-last_action))
            control_action.append(-gamma *reward)
            gamma *= 0.99
            # done = False
            if done:
                success_num += 1
                final_step_list.append(step+1)
                control_cost_list.append(np.sum(np.asarray(control_action)))
                break
            action_list.append(last_action-action)
            state_list.append(next_state)
            last_action = np.copy(action)
            state = next_state
            
        
        if not done:
            final_step_list.append(step_num)
            control_cost_list.append(np.sum(np.asarray(control_action)))
            # print(state)
            # print(action)
            # exit(0)
        final_state_list.append(next_state)
        final_reward_list.append(reward)
        
    print(f'result for {algm}')
    print(success_num)
    print(np.mean(final_step_list), np.std(final_step_list))
    print('cost',np.mean(control_cost_list), np.std(control_cost_list))
    print('final reward', np.mean(final_reward_list))
    return final_state_list

def plot_bar(num_agent):
    print('max')
    xlab = 'Voltage Violation (per test scenario)'
    plt.rcParams['font.size'] = '11'
    
    plt.figure(figsize=(3.96,2.87),dpi=300)
    plt.gcf().subplots_adjust(bottom=0.17,left=0.15)
    marks=[1.0,0,0,0,0]
    bars=('Stable','5-7%','7-9%','9-10%','>10%')
    y=np.arange(len(bars))
    plt.bar(y+0.2,marks, 0.4,color='b',label='Stable-DDPG')
    state_list = test_suc_rate('ddpg',step_num=100)
    state_list = np.asarray(state_list)
    state_list = np.abs(state_list-1)
    # print(state_list.shape)
    
    # np.savetxt('state13.txt',state_list)
    # state_list = np.loadtxt('state123h.txt')
    if ph_num == 3:
        state_list_3p = []
        for i in range(num_agent):
            phases = env.injection_bus[env.injection_bus_str[i]]
            id = get_id(phases)
            if len(id)>1:
                # print(np.max(state_list[:,i,id],axis=1).shape)
                state_list_3p.append(np.reshape(np.max(state_list[:,i,id],axis=1), (len(state_list),1)))
            else:
                state_list_3p.append(np.reshape(state_list[:,i,id], (len(state_list),1)))
        state_list_3p = np.concatenate(state_list_3p,axis=1)
    
    # print(state_list_3p.shape)
    # exit(0)
    if ph_num == 3:
        state_list = state_list_3p
    state_list = np.max(state_list,axis=1)
    # state_list = np.abs(state_list-1)
    marks = [0,0,0,0,0]
    marks[0]=np.sum(state_list<0.05)
    # print(state)
    marks[1]=np.sum(state_list<0.07)-marks[0]
    marks[2]=np.sum(state_list<0.09)-marks[1]-marks[0]
    marks[3]=np.sum(state_list<0.1)-marks[1]-marks[0]-marks[2]
    if ph_num == 3:
        num_agent = state_list_3p.shape[1]
    marks[4]=len(state_list)-marks[1]-marks[0]-marks[2]-marks[3]
    marks = np.array(marks)/len(state_list)
    print(marks)
    plt.bar(y-0.2,marks,0.4,color='r',label='DDPG')
    
    plt.xticks(y,bars)
    plt.xlabel(xlab)
    plt.ylabel('Frequency')
    plt.legend(loc='upper right')
    plt.show()

def plot_bar_avg(num_agent):
    print('avg')
    xlab = 'Voltage Violation (per bus)'
    plt.rcParams['font.size'] = '11'
    
    plt.figure(figsize=(3.96,2.87),dpi=300)
    plt.gcf().subplots_adjust(bottom=0.17,left=0.15)
    marks=[1.0,0,0,0,0]
    bars=('Stable','5-7%','7-9%','9-10%','>10%')
    y=np.arange(len(bars))
    plt.bar(y+0.2,marks, 0.4,color='b',label='Stable-DDPG')
    state_list = test_suc_rate('ddpg',step_num=100)
    state_list = np.asarray(state_list)
    if ph_num == 3:
        state_list_3p = []
        for i in range(num_agent):
            phases = env.injection_bus[env.injection_bus_str[i]]
            id = get_id(phases)
            state_list_3p.append(state_list[:,i,id])
        state_list_3p = np.concatenate(state_list_3p,axis=1)
    # print(state_list_3p.shape)
    # exit(0)
    if ph_num == 3:
        state_list = state_list_3p
    state_list = np.abs(state_list-1)
    marks = [0,0,0,0,0]
    marks[0]=np.sum(state_list<0.05)
    marks[1]=np.sum(state_list<0.07)-marks[0]
    marks[2]=np.sum(state_list<0.09)-marks[1]-marks[0]
    marks[3]=np.sum(state_list<0.1)-marks[1]-marks[0]-marks[2]
    if ph_num == 3:
        num_agent = state_list_3p.shape[1]
    marks[4]=len(state_list)*num_agent-marks[1]-marks[0]-marks[2]-marks[3]
    marks = np.array(marks)/len(state_list)/num_agent
    print(marks)
    plt.bar(y-0.2,marks,0.4,color='r',label='DDPG')
    
    plt.xticks(y,bars)
    plt.xlabel(xlab)
    plt.ylabel('Frequency')
    plt.legend(loc='upper right')
    plt.show()
#11, 36, 75
def plot_traj_123_broken(seed):
    color_set = ['#1f77b4', '#ff7f0e', '#d62728', '#9467bd', '#8c564b'] #'#2ca02c', 
    line_type = ['-.','-']
    fig, axs = plt.subplots(2, 1, figsize=(6,6), sharex=True, gridspec_kw={'height_ratios': [5,1]})
    ddpg_plt=[]
    safe_plt = []
    ddpg_a_plt=[]
    safe_a_plt = []
    state = env.reset(seed)
    episode_reward = 0
    last_action = np.zeros((num_agent,1))
    action_list=[]
    state_list =[]
    state_list.append(state)
    step_num=350
    id1 = 3
    id2 = 8
    for step in range(step_num):
        action = []
        for i in range(num_agent):
            # sample action according to the current policy and exploration noise
            action_agent = linear_agent_list[i].policy_net.get_action(np.asarray([state[i]]),last_action[i])
            action.append(action_agent)

        # PI policy    
        action = last_action + np.asarray(action)
        # execute action a_t and observe reward r_t and observe next state s_{t+1}
        next_state, reward, reward_sep, done = env.step_Preward(action, (action-last_action))
        # if done:
        #     print("finished")
        action_list.append(action)
        state_list.append(next_state)
        last_action = np.copy(action)
        state = next_state
    for idx,i in enumerate([id1,id2]): 
        axs[0].plot(range(len(action_list)), np.array(action_list)[:,i], line_type[idx], label = f'Safe gradient flow at bus {injection_bus[i]+1}', linewidth=2,color=color_set[0])
        axs[1].plot(range(len(action_list)), np.array(action_list)[:,i], line_type[idx], label = f'Safe gradient flow at bus {injection_bus[i]+1}', linewidth=2,color=color_set[0])

    state = env.reset(seed)
    episode_reward = 0
    last_action = np.zeros((num_agent,1))
    action_list=[]
    state_list =[]
    state_list.append(state)
    for step in range(step_num):
        action = []
        for i in range(num_agent):
            # sample action according to the current policy and exploration noise
            action_agent = ddpg_agent_list[i].policy_net.get_action(np.asarray([state[i]]),last_action[i])
            action.append(action_agent)

        # PI policy    
        action = last_action + np.asarray(action)

        # execute action a_t and observe reward r_t and observe next state s_{t+1}
        next_state, reward, reward_sep, done = env.step_Preward(action, (action-last_action))
        # if done:
        #     print("finished")
        action_list.append(action)
        state_list.append(next_state)
        last_action = np.copy(action)
        state = next_state
    axs[0].plot(np.asarray(range(len(action_list)+5))-5,np.ones_like(range(len(action_list)+5))*(-4.725),'--', linewidth=2,color='dimgray')
    # lb = axs[0].plot(range(len(action_list)), [0.95]*len(action_list), linestyle='--', dashes=(5, 10), color='g', label='lower bound')
    # ub = axs[0].plot(range(len(action_list)), [1.05]*len(action_list), linestyle='--', dashes=(5, 10), color='r', label='upper bound')
    for idx,i in enumerate([id1,id2]):    #[2,5,8]
        dps = axs[0].plot(range(len(action_list)), np.array(action_list)[:,i], line_type[idx], label = f'Stable-DDPG at bus {injection_bus[i]+1}', linewidth=2,color=color_set[1])
        dpa = axs[1].plot(range(len(action_list)), np.array(action_list)[:,i], line_type[idx], label = f'Stable-DDPG at bus {injection_bus[i]+1}', linewidth=2,color=color_set[1])
        ddpg_plt.append(dps)
        ddpg_a_plt.append(dpa)

    state = env.reset(seed)
    episode_reward = 0
    last_action = np.zeros((num_agent,1))
    action_list=[]
    state_list =[]
    state_list.append(state)
    for step in range(step_num):
        action = []
        for i in range(num_agent):
            # sample action according to the current policy and exploration noise
            action_agent = safe_ddpg_agent_list[i].policy_net.get_action(np.asarray([state[i]]),last_action[i])#+np.random.normal(0, 0.05)
            # action_agent = (np.maximum(state[i]-1.05, 0)-np.maximum(0.95-state[i], 0)).reshape((1,))*2
            # action_agent = np.clip(action_agent, -max_ac, max_ac)
            action.append(action_agent)

        # PI policy    
        action = last_action + np.asarray(action)

        # execute action a_t and observe reward r_t and observe next state s_{t+1}
        next_state, reward, reward_sep, done = env.step_Preward(action, (action-last_action))
        # if done:
        #     print("finished")
        action_list.append(action)
        state_list.append(next_state)
        last_action = np.copy(action)
        state = next_state
    for idx,i in enumerate([id1,id2]): 
        axs[0].plot(range(len(action_list)), np.array(action_list)[:,i], line_type[idx], label = f'TASRL at bus {injection_bus[i]+1}', linewidth=2,color=color_set[2])
        axs[1].plot(range(len(action_list)), np.array(action_list)[:,i], line_type[idx], label = f'TASRL at bus {injection_bus[i]+1}', linewidth=2,color=color_set[2])

    
    matplotlib.rcParams['text.usetex']=True
    # axs[0].plot(range(len(action_list)),np.ones_like(np.array(state_list)[:len(action_list),0])*1.05,'--', linewidth=2,color='dimgray')  #,label=r'$\bar{v}$'
    # axs[1].plot(range(len(action_list)),np.ones_like(np.array(state_list)[:len(action_list),0])*(-21.6), ':',linewidth=2,label=r'$\underline{q}$'+f' at bus {injection_bus[2]+1}',color=color_set[4]) 
    axs[1].plot(np.asarray(range(len(action_list)+5))-5,np.ones_like(range(len(action_list)+5))*(-10.8),'--', linewidth=2,color='dimgray') 
    axs[0].set_ylim(-7, 0)  # outliers only
    axs[1].set_ylim(-11, -10.5)  # most of the data
    axs[0].set_xlim(-5, step_num)
    axs[1].set_xlim(-5, step_num)
    axs[0].spines['bottom'].set_visible(False)
    axs[1].spines['top'].set_visible(False)
    axs[0].xaxis.tick_top()
    axs[0].tick_params(labeltop=False)  # don't put tick labels at the top
    d = .25  # proportion of vertical to horizontal extent of the slanted line
    kwargs = dict(marker=[(-1, -d), (1, d)], markersize=12,
                linestyle="none", color='k', mec='k', mew=1, clip_on=False)
    axs[0].plot([0, 1], [0, 0], transform=axs[0].transAxes, **kwargs)
    axs[1].plot([0, 1], [1, 1], transform=axs[1].transAxes, **kwargs)
    plt.subplots_adjust(
        top=0.9, bottom=0.214, left=0.15, right=0.9, hspace=0.133, wspace=0.062
    )
    plt.show()

def plot_traj_123(seed):
    color_set = ['#1f77b4', '#ff7f0e',  '#d62728', '#9467bd', '#8c564b'] #'#2ca02c',
    line_type = ['-.','-']
    fig, axs = plt.subplots(1, 2, figsize=(13,6))
    ddpg_plt=[]
    safe_plt = []
    ddpg_a_plt=[]
    safe_a_plt = []
    state = env.reset(seed)
    episode_reward = 0
    last_action = np.zeros((num_agent,1))
    action_list=[]
    state_list =[]
    state_list.append(state)
    step_num=350
    id1 = 3
    id2 = 8
    for step in range(step_num):
        action = []
        for i in range(num_agent):
            # sample action according to the current policy and exploration noise
            action_agent = linear_agent_list[i].policy_net.get_action(np.asarray([state[i]]),last_action[i])#+np.random.normal(0, 0.05)
            # action_agent = (np.maximum(state[i]-1.05, 0)-np.maximum(0.95-state[i], 0)).reshape((1,))*2
            # action_agent = np.clip(action_agent, -max_ac, max_ac)
            action.append(action_agent)

        # PI policy    
        action = last_action + np.asarray(action)

        # execute action a_t and observe reward r_t and observe next state s_{t+1}
        next_state, reward, reward_sep, done = env.step_Preward(action, (action-last_action))
        # if done:
        #     print("finished")
        action_list.append(action)
        state_list.append(next_state)
        last_action = np.copy(action)
        state = next_state
    for idx,i in enumerate([id1,id2]): 
        axs[0].plot(range(len(action_list)), np.array(state_list)[:len(action_list),i], line_type[idx], label = f'Safe gradient flow at bus {injection_bus[i]+1}', linewidth=2,color=color_set[0])
        axs[1].plot(range(len(action_list)), np.array(action_list)[:,i], line_type[idx], label = f'Safe gradient flow at bus {injection_bus[i]+1}', linewidth=2,color=color_set[0])

    state = env.reset(seed)
    episode_reward = 0
    last_action = np.zeros((num_agent,1))
    action_list=[]
    state_list =[]
    state_list.append(state)
    for step in range(step_num):
        action = []
        for i in range(num_agent):
            # sample action according to the current policy and exploration noise
            action_agent = ddpg_agent_list[i].policy_net.get_action(np.asarray([state[i]]),last_action[i])
            action.append(action_agent)

        # PI policy    
        action = last_action + np.asarray(action)

        # execute action a_t and observe reward r_t and observe next state s_{t+1}
        next_state, reward, reward_sep, done = env.step_Preward(action, (action-last_action))
        # if done:
        #     print("finished")
        action_list.append(action)
        state_list.append(next_state)
        last_action = np.copy(action)
        state = next_state
    axs[1].plot(range(len(action_list)),np.ones_like(np.array(state_list)[:len(action_list),0])*(-4.725),'--', linewidth=2,color='dimgray') #,label=r'$\underline{q}$'+f' at bus {injection_bus[8]+1}'
    axs[1].plot(range(len(action_list)),np.ones_like(np.array(state_list)[:len(action_list),0])*(-10.8),'--', linewidth=2,color='dimgray') 
    # lb = axs[0].plot(range(len(action_list)), [0.95]*len(action_list), linestyle='--', dashes=(5, 10), color='g', label='lower bound')
    # ub = axs[0].plot(range(len(action_list)), [1.05]*len(action_list), linestyle='--', dashes=(5, 10), color='r', label='upper bound')
    for idx,i in enumerate([id1,id2]):    #[2,5,8]
        dps = axs[0].plot(range(len(action_list)), np.array(state_list)[:len(action_list),i], line_type[idx], label = f'Stable-DDPG at bus {injection_bus[i]+1}', linewidth=2,color=color_set[1])
        dpa = axs[1].plot(range(len(action_list)), np.array(action_list)[:,i], line_type[idx], label = f'Stable-DDPG at bus {injection_bus[i]+1}', linewidth=2,color=color_set[1])
        ddpg_plt.append(dps)
        ddpg_a_plt.append(dpa)

    state = env.reset(seed)
    episode_reward = 0
    last_action = np.zeros((num_agent,1))
    action_list=[]
    state_list =[]
    state_list.append(state)
    for step in range(step_num):
        action = []
        for i in range(num_agent):
            # sample action according to the current policy and exploration noise
            action_agent = safe_ddpg_agent_list[i].policy_net.get_action(np.asarray([state[i]]),last_action[i])#+np.random.normal(0, 0.05)
            # action_agent = (np.maximum(state[i]-1.05, 0)-np.maximum(0.95-state[i], 0)).reshape((1,))*2
            # action_agent = np.clip(action_agent, -max_ac, max_ac)
            action.append(action_agent)

        # PI policy    
        action = last_action + np.asarray(action)

        # execute action a_t and observe reward r_t and observe next state s_{t+1}
        next_state, reward, reward_sep, done = env.step_Preward(action, (action-last_action))
        # if done:
        #     print("finished")
        action_list.append(action)
        state_list.append(next_state)
        last_action = np.copy(action)
        state = next_state
    for idx,i in enumerate([id1,id2]): 
        axs[0].plot(range(len(action_list)), np.array(state_list)[:len(action_list),i], line_type[idx], label = f'TASRL at bus {injection_bus[i]+1}', linewidth=2,color=color_set[2])
        axs[1].plot(range(len(action_list)), np.array(action_list)[:,i], line_type[idx], label = f'TASRL at bus {injection_bus[i]+1}', linewidth=2,color=color_set[2])

    
    matplotlib.rcParams['text.usetex']=True
    axs[0].plot(np.asarray(range(len(action_list)+5))-5,np.ones_like(range(len(action_list)+5))*(1.05),'--', linewidth=2,color='dimgray') #,label=r'$\bar{v}$'
    # axs[1].plot(range(len(action_list)),np.ones_like(np.array(state_list)[:len(action_list),0])*(-21.6), ':',linewidth=2,label=r'$\underline{q}$'+f' at bus {injection_bus[2]+1}',color=color_set[4]) 
    
    axs[0].set_xlim(-5, step_num)
    axs[1].set_xlim(-5, step_num)
    # leg1 = plt.legend(safe_a_plt, safe_name, loc='lower left')
    # axs[0].legend(loc='lower left', prop={"size":20})
    # axs[1].legend(loc='lower left', prop={"size":20})
    box = axs[0].get_position()
    axs[0].set_position([box.x0-0.05*box.width, box.y0+0.2*box.height,
                    box.width* 0.95, box.height*0.8])
    box = axs[1].get_position()
    axs[1].set_position([box.x0+0.05*box.width, box.y0+0.2*box.height,
                    box.width* 0.95, box.height*0.8])
    axs[0].legend(loc='lower center', bbox_to_anchor=(1.2, -0.4),
        fancybox=True, shadow=True, ncol=3,prop={"size":13})
    # axs[1].legend(loc='upper right', prop={"size":13})
    axs[0].set_xlabel('Iteration Steps')   
    axs[1].set_xlabel('Iteration Steps')  
    axs[0].set_ylabel('Bus Voltage [p.u.]')   
    axs[1].set_ylabel('q Injection [MVar]')  
    plt.show()

def plot_F(seed):
    color_set = ['#1f77b4', '#ff7f0e', '#d62728', '#9467bd', '#8c564b']
    fig = plt.figure(figsize=(6.5,6))
    axs = fig.add_subplot(1, 1, 1)

    state = env.reset(seed)
    episode_reward = 0
    last_action = np.zeros((num_agent,1))
    action_list=[]
    state_list =[]
    n_step = 350
    # state_list.append(state)
    for step in range(n_step):
        action = []
        for i in range(num_agent):
            # sample action according to the current policy and exploration noise
            action_agent = linear_agent_list[i].policy_net.get_action(np.asarray([state[i]]),last_action[i])#+np.random.normal(0, 0.05)
            action.append(action_agent)

        # PI policy    
        action = last_action + np.asarray(action)

        # execute action a_t and observe reward r_t and observe next state s_{t+1}
        next_state, reward, reward_sep, done = env.step_Preward(action, (action-last_action))
        # if done:
        #     print("finished")
        action_list.append(action)
        state_list.append(-reward)
        if step<1:
            continue
        elif state_list[step]>state_list[step-1]:
            print('violtate')
        last_action = np.copy(action)
        state = next_state
    
    axs.plot(range(len(state_list)), np.array(state_list)[:], '-', label = f'Safe gradient flow', linewidth=2,color=color_set[0])
    state = env.reset(seed)
    last_action = np.zeros((num_agent,1))
    state_list =[]
    action_list=[]
    # state_list.append(state)
    for step in range(n_step):
        action = []
        for i in range(num_agent):
            # sample action according to the current policy and exploration noise
            action_agent = ddpg_agent_list[i].policy_net.get_action(np.asarray([state[i]]),last_action[i])
            action.append(action_agent)

        # PI policy    
        action = last_action + np.asarray(action)

        # execute action a_t and observe reward r_t and observe next state s_{t+1}
        next_state, reward, reward_sep, done = env.step_Preward(action, (action-last_action))
        # if done:
        #     print("finished")
        state_list.append(-reward)
        last_action = np.copy(action)
        state = next_state
    
    axs.plot(range(len(state_list)), np.array(state_list)[:], '-', label = f'Stable-DDPG', linewidth=2,color=color_set[1])


    state = env.reset(seed)
    episode_reward = 0
    last_action = np.zeros((num_agent,1))
    action_list=[]
    state_list =[]
    # state_list.append(state)
    for step in range(n_step):
        action = []
        for i in range(num_agent):
            # sample action according to the current policy and exploration noise
            action_agent = safe_ddpg_agent_list[i].policy_net.get_action(np.asarray([state[i]]),last_action[i])#+np.random.normal(0, 0.05)
            # action_agent = (np.maximum(state[i]-1.05, 0)-np.maximum(0.95-state[i], 0)).reshape((1,))*2
            # action_agent = np.clip(action_agent, -max_ac, max_ac)
            action.append(action_agent)

        # PI policy    
        action = last_action + np.asarray(action)

        # execute action a_t and observe reward r_t and observe next state s_{t+1}
        next_state, reward, reward_sep, done = env.step_Preward(action, (action-last_action))
        action_list.append(action)
        state_list.append(-reward)
        last_action = np.copy(action)
        state = next_state
    
    axs.plot(range(len(state_list)), np.array(state_list)[:], '-', label = f'TASRL', linewidth=2,color=color_set[2])
    
    axs.set_ylabel('Objective F(q)')   
    axs.set_xlabel('Iteration Steps')  
    plt.xticks(fontsize=20, rotation=0)
    # axs.legend()
    plt.subplots_adjust(
        top=0.9, bottom=0.214, left=0.15, right=0.9, hspace=0.133, wspace=0.062
    )
    # plt.close()
    plt.show()

def plot_action_selcted(selected=[1,5,9],plot_safe=True, plot_linear=True,plot_ddpg=True):    
    color_set = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b']
    # if ph_num ==3:
    #     injection_bus = [3,6,5,2,4,9,12,10,11,1,8,7]
    fig, axs = plt.subplots(1, 3, figsize=(12,4))
    plt.gcf().subplots_adjust(bottom=0.18)
    if plot_safe:
        for indx,i in enumerate(selected):
            # plot policy
            N = 30
            s_array = np.zeros(N,)
            
            a_array_baseline = np.zeros(N,)
            safe_ddpg_a_array = np.zeros((N,ph_num))
            
            for j in range(N):
                if ph_num == 1:
                    state = np.array([0.85+0.01*j])
                    s_array[j] = state
                else:
                    state = np.resize(np.array([0.85+0.01*j]),(3))
                    s_array[j] = state[0] 
                
                if ph_num == 3: 
                    safe_ddpg_action = np.zeros(3)
                    phases = env.injection_bus[env.injection_bus_str[i]]
                    id = get_id(phases)
                    action_tmp = safe_ddpg_agent_list[i].policy_net.get_action(np.asarray([state[id]])) 
                    action_tmp = action_tmp.reshape(len(id),)  
                    for p in range(len(phases)):
                        safe_ddpg_action[id[p]]=action_tmp[p]
                else:          
                    safe_ddpg_action = safe_ddpg_agent_list[i].policy_net.get_action(np.asarray([state]),np.zeros_like(np.asarray([state])))
                # safe_ddpg_action = np.clip(safe_ddpg_action, -max_ac, max_ac)
                safe_ddpg_a_array[j] = safe_ddpg_action
            if ph_num == 1:
                axs[indx].plot(s_array, safe_ddpg_a_array, label = r'$\pi_\theta+\nabla F$', linewidth=2)
            else:
                phases = env.injection_bus[env.injection_bus_str[i]]
                id = get_id(phases)
                for cnt, ph_id in enumerate(id):
                    clr = color_set[ph_id]
                    axs[indx].plot(s_array, safe_ddpg_a_array[:,ph_id], label = f'Stable-DDPG-{phases[cnt]}', linewidth=2, color=clr)
            axs[indx].set_xlabel('voltage magnitude [p.u.]', size=18)   
            axs[0].set_ylabel(f'q change [MVar]', size=18) 
    if plot_linear:
        for indx,i in enumerate(selected):
            # plot policy
            N = 30
            s_array = np.zeros(N,)
            
            a_array_baseline = np.zeros(N,)
            linear_a_array = np.zeros((N,ph_num))
            
            for j in range(N):
                if ph_num == 1:
                    state = np.array([0.85+0.01*j])
                    s_array[j] = state
                else:
                    state = np.resize(np.array([0.85+0.01*j]),(3))
                    s_array[j] = state[0] 
                
                if ph_num == 3: 
                    safe_ddpg_action = np.zeros(3)
                    phases = env.injection_bus[env.injection_bus_str[i]]
                    id = get_id(phases)
                    action_tmp = linear_agent_list[i].policy_net.get_action(np.asarray([state[id]])) 
                    action_tmp = action_tmp.reshape(len(id),)  
                    for p in range(len(phases)):
                        safe_ddpg_action[id[p]]=action_tmp[p]
                else:          
                    safe_ddpg_action = linear_agent_list[i].policy_net.get_action(np.asarray([state]),np.zeros_like(np.asarray([state])))
                # safe_ddpg_action = np.clip(safe_ddpg_action, -max_ac, max_ac)
                linear_a_array[j] = safe_ddpg_action
            if ph_num == 1:
                axs[indx].plot(s_array, linear_a_array, ':', label = r'$\nabla F$', linewidth=2, color='r')
            else:
                phases = env.injection_bus[env.injection_bus_str[i]]
                id = get_id(phases)
                axs[indx].plot(s_array, linear_a_array[:,id[0]], ':', label = f'Linear', linewidth=2, color='r')
    if plot_ddpg:            
        for indx,i in enumerate(selected):
            # plot policy
            N = 30
            s_array = np.zeros(N,)
            
            a_array_baseline = np.zeros(N,)
            ddpg_a_array = np.zeros((N,ph_num))
            # safe_ddpg_a_array = np.zeros(N,)
            
            for j in range(N):
                if ph_num == 1:
                    state = np.array([0.85+0.01*j])
                    s_array[j] = state
                else:
                    state = np.resize(np.array([0.85+0.01*j]),(3))
                    s_array[j] = state[0] 

                # action_baseline = (np.maximum(state[0]-1.03, 0)-np.maximum(0.97-state[0], 0)).reshape((1,))*slope

                if ph_num == 3: 
                    ddpg_action = np.zeros(3)
                    phases = env.injection_bus[env.injection_bus_str[i]]
                    id = get_id(phases)
                    action_tmp = ddpg_agent_list[i].policy_net.get_action(np.asarray([state[id]])) 
                    action_tmp = action_tmp.reshape(len(id),)  
                    for p in range(len(phases)):
                        ddpg_action[id[p]]=action_tmp[p]
                else:          
                    ddpg_action = ddpg_agent_list[i].policy_net.get_action(np.asarray([state]),np.zeros_like(np.asarray([state])))
                # ddpg_action = np.clip(ddpg_action, -max_ac, max_ac)
                # action_baseline = np.clip(action_baseline, -max_ac, max_ac)
                # a_array_baseline[j] = -action_baseline[0]
                ddpg_a_array[j] = ddpg_action
            if indx != 0:
                axs[indx].get_yaxis().set_visible(False)
            axs[indx].set_title(f'Bus {injection_bus[i]+1}', size=20)
            if ph_num==1:
                axs[indx].plot(s_array, ddpg_a_array, '--', label = r'$\pi_\theta$', linewidth=2)
            else:
                phases = env.injection_bus[env.injection_bus_str[i]]
                id = get_id(phases)
                for cnt, ph_id in enumerate(id):
                    clr = color_set[ph_id+3]
                    axs[indx].plot(s_array, ddpg_a_array[:,ph_id], '--', label = f'DDPG-{phases[cnt]}', linewidth=2, color=clr)
        # axs[indx].plot(s_array, a_array_baseline, ':', label = 'Linear',color='r', linewidth=2)
        
        # axs[indx].legend(loc='lower left', prop={"size":13})
            
        
    box = axs[0].get_position()
    axs[0].set_position([box.x0-0.15*box.width, box.y0,
                    box.width* 0.9, box.height])
    box = axs[1].get_position()
    axs[1].set_position([box.x0-0.19*box.width, box.y0,
                    box.width* 0.9, box.height])
    box = axs[2].get_position()
    axs[2].set_position([box.x0-0.22*box.width, box.y0,
                    box.width* 0.9, box.height])
    axs[0].legend(loc='right', bbox_to_anchor=(4.45, 0.4),
        fancybox=True, shadow=True, ncol=1, prop={"size":13})
    plt.show()

def get_id(phases):
    if phases == 'abc':
        id = [0,1,2]
    elif phases == 'ab':
        id = [0,1]
    elif phases == 'ac':
        id = [0,2]
    elif phases == 'bc':
        id = [1,2]
    elif phases == 'a':
        id = [0]
    elif phases == 'b':
        id = [1]
    elif phases == 'c':
        id = [2]
    else:
        print("error!")
        exit(0)
    return id


if __name__ == "__main__":
    # print("test")
    test_suc_rate('safe-ddpg',step_num=100) #safe-ddpg
    # test_suc_rate('linear',step_num=1000)
    # test_suc_rate('ddpg',step_num=100)
    # plot_bar_avg(len(injection_bus))
    # plot_bar(len(injection_bus))
    # test_suc_rate('linear')
    # plot_action_selcted()
    # plot_bar(len(injection_bus))
    # plot_traj_123_broken(17)#3,5
    # plot_traj_123(17)
    # plot_action_selcted([1,4,9]) #13b3p
    # plot_action_selcted([2,4,10]) #13b3p
    # plot_action_selcted([2,5,8],True,True,True) #123b 2,5,8
    # plot_action_selcted([0,1,2])
    
    plot_F(17)#13
